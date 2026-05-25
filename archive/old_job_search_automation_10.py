"""
Job Search Automation — Customer Success Manager, B2B SaaS, Tel Aviv
=====================================================================
100% free. No RSS. No Apify. No paid APIs.

Sources scraped (BeautifulSoup + requests / Selenium):
  - StartupForStartup  (startupforstartup.com)
  - Nefesh B'Nefesh    (nbn.org.il)
  - JobShop            (jobshop.co.il)
  - LinkedIn           (best-effort, graceful fail if blocked)
  - Indeed IL          (il.indeed.com)  ← NEW

ONE-TIME SETUP
  pip install requests beautifulsoup4 python-dotenv selenium webdriver-manager

Create a .env file in the same folder:
  EMAIL_FROM=you@gmail.com
  EMAIL_TO=you@gmail.com
  EMAIL_PASSWORD=xxxx xxxx xxxx xxxx   <- 16-char Gmail App Password

SCHEDULE FREE (weekdays 10am Israel time via GitHub Actions)
  See SCHEDULING section at the bottom.
"""

import os
import re
import json
import time
import hashlib
import smtplib
import datetime
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

load_dotenv()

REQUEST_DELAY = 2  # seconds between requests — polite to servers

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,he;q=0.8,fr;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def get_selenium_driver():
    """Return a headless Chrome driver. Shared across scrapers."""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options,
    )
    driver.set_page_load_timeout(20)
    return driver


# =============================================================================
# 1. TITLE VARIANTS
# =============================================================================

TITLE_VARIANTS = [
    # Core CSM
    "customer success manager",
    "customer success lead",
    "customer success director",
    "head of customer success",
    "vp of customer success",
    "vp customer success",
    "director of customer success",
    "chief customer officer",
    # Founder / first-hire framing
    "founding customer success",
    "first customer success",
    "customer success team lead",
    "customer success department head",
    # Adjacent titles common in Israeli SaaS
    "client success manager",
    "client success lead",
    "account success manager",
    "customer experience manager",
    "customer experience lead",
    "customer onboarding manager",
    "customer engagement manager",
    "customer retention manager",
    "customer lifecycle manager",
    # Account Management overlap in B2B SaaS
    "strategic account manager",
    "enterprise account manager",
    "technical account manager",
    # Fintech-specific
    "client relations manager",
    "relationship manager",
    # French titles
    "responsable customer success",
    "charge de succes client",
    "gestionnaire de succes client",
    # Customer Care
    "customer care manager",
    "customer care lead",
    "customer care director",
    "head of customer care",
    "vp customer care",
    "customer care team lead",

    # Hebrew forms found on Israeli boards
    "\u05de\u05e0\u05d4\u05dc \u05d4\u05e6\u05dc\u05d7\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",
    "\u05de\u05e0\u05d4\u05dc\u05ea \u05d4\u05e6\u05dc\u05d7\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",
    "\u05de\u05e0\u05d4\u05dc \u05e9\u05d9\u05e8\u05d5\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",
    "\u05de\u05e0\u05d4\u05dc\u05ea \u05e9\u05d9\u05e8\u05d5\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",
]


def matches_title(job_title: str) -> bool:
    """Case-insensitive substring match with punctuation normalisation."""
    normalised = re.sub(r"[^\w\s]", " ", job_title.lower())
    normalised = re.sub(r"\s+", " ", normalised).strip()
    return any(v in normalised for v in TITLE_VARIANTS)


# =============================================================================
# 2. FILTER LOGIC  (no date restriction)
# =============================================================================

LOCATION_INCLUDE = [
    "tel aviv", "tlv", "israel",
    "\u05ea\u05dc \u05d0\u05d1\u05d9\u05d1", "\u05d9\u05e9\u05e8\u05d0\u05dc",
    "ramat gan", "herzliya", "petah tikva", "bnei brak",
    "remote", "hybrid",
]

COMPANY_TYPE_INCLUDE = [
    "saas", "b2b", "fintech", "financial technology",
    "software", "platform", "startup", "scale-up",
    "series a", "series b", "seed", "venture",
]

EXCLUDE_KEYWORDS = [
    "b2c", "e-commerce", "gaming", "game",
    "agency", "outsourcing", "bpo", "call center",
    "internship", "intern", "student", "apprentice",
]

# Israel-only boards — skip the location gate for these
ISRAEL_NATIVE_SOURCES = {
    "startupforstartup", "lastartup", "jobshop", "nefesh b'nefesh", "indeed il"
}


def passes_filters(job: dict) -> bool:
    """
    Returns True if the job passes all gates.
    Sets job['saas_signal'] as a soft badge (never excludes).
    No date restriction — all listings are considered.
    """
    title       = (job.get("title", "") or "").lower()
    location    = (job.get("location", "") or "").lower()
    description = (job.get("description", "") or "").lower()
    company     = (job.get("company", "") or "").lower()
    source      = (job.get("source", "") or "").lower()

    full_text = f"{title} {location} {description} {company}"

    # Gate 1 — title must match
    if not matches_title(job.get("title", "")):
        return False

    # Gate 2 — location (skip for Israel-native boards)
    if not any(s in source for s in ISRAEL_NATIVE_SOURCES):
        if not any(loc in full_text for loc in LOCATION_INCLUDE):
            return False

    # Gate 3 — hard exclusions
    if any(kw in full_text for kw in EXCLUDE_KEYWORDS):
        return False

    # Gate 4 (soft) — SaaS/Fintech badge
    job["saas_signal"] = any(kw in full_text for kw in COMPANY_TYPE_INCLUDE)

    return True


# =============================================================================
# 3. DEDUPLICATION CACHE
# =============================================================================

CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "seen_jobs_cache.json"
)


def load_cache() -> set:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
        except (json.JSONDecodeError, IOError):
            print("  [!] Cache corrupted — starting fresh")
    return set()


def save_cache(seen_ids: set):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen_ids)), f, indent=2)
    except IOError as e:
        print(f"  [!] Could not save cache: {e}")


def make_job_id(job: dict) -> str:
    company = re.sub(r"\s+", " ", (job.get("company") or "").lower().strip())
    title   = re.sub(r"\s+", " ", (job.get("title")   or "").lower().strip())
    return hashlib.md5(f"{company}|{title}".encode()).hexdigest()


# =============================================================================
# 4. SCRAPERS
# =============================================================================

def safe_get(url: str, source_name: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        if not resp.text.strip():
            print(f"  [{source_name}] Empty response")
            return None
        return BeautifulSoup(resp.text, "html.parser")
    except requests.exceptions.Timeout:
        print(f"  [{source_name}] Timed out — skipping")
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code
        if code == 403:
            print(f"  [{source_name}] Blocked (403) — try again from your PC")
        else:
            print(f"  [{source_name}] HTTP {code}")
    except requests.exceptions.ConnectionError:
        print(f"  [{source_name}] Connection error")
    except Exception as e:
        print(f"  [{source_name}] Unexpected: {e}")
    return None


# ── StartupForStartup (Selenium) ──────────────────────────────────────────────

def fetch_startup_for_startup() -> list:
    SOURCE   = "StartupForStartup"
    BASE_URL = "https://www.startupforstartup.com"
    jobs     = []
    driver   = None

    search_terms = [
        "customer success",
        "\u05d4\u05e6\u05dc\u05d7\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",
        "\u05de\u05e0\u05d4\u05dc \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",
        "\u05d7\u05d5\u05d5\u05d9\u05d9\u05ea \u05dc\u05e7\u05d5\u05d7",
    ]

    try:
        driver = get_selenium_driver()
        for term in search_terms:
            encoded = requests.utils.quote(term)
            url = f"{BASE_URL}/jobs-in-startups/?s={encoded}"
            try:
                driver.get(url)
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.job-mini-card-wrap"))
                )
                time.sleep(2)
            except Exception:
                driver.get(f"{BASE_URL}/jobs-in-startups/")
                time.sleep(3)

            soup  = BeautifulSoup(driver.page_source, "html.parser")
            cards = soup.select("div.job-mini-card-wrap")

            for card in cards[:40]:
                try:
                    title_el = card.select_one("div.job-mini-card-logo-title h4") or \
                               card.select_one("div.job-mini-card-logo-title")
                    spans    = card.select("div.job-mini-card-logo-title p span")
                    company  = spans[-1].get_text(strip=True) if spans else ""
                    data_id  = card.get("data-id", "")
                    url_full = f"{BASE_URL}/seekers-form/?job_apply_id={data_id}" if data_id else ""
                    title    = title_el.get_text(strip=True) if title_el else ""
                    date_el  = card.select_one("div.job-mini-card-logo-title p span:first-child")
                    posted_date = date_el.get_text(strip=True) if date_el else ""

                    if title and url_full:
                        jobs.append({
                            "title": title, "company": company,
                            "location": "Tel Aviv, Israel", "url": url_full,
                            "description": "", "source": SOURCE,
                            "posted_date": posted_date,
                        })
                except Exception:
                    continue
            time.sleep(REQUEST_DELAY)

    except Exception as e:
        print(f"  [{SOURCE}] Selenium error: {e}")
    finally:
        if driver:
            driver.quit()

    seen, unique = set(), []
    for j in jobs:
        k = make_job_id(j)
        if k not in seen:
            seen.add(k); unique.append(j)
    print(f"  {SOURCE}: {len(unique)} listings")
    return unique


# ── Nefesh B'Nefesh (Selenium) ───────────────────────────────────────────────

def fetch_nefesh_bnefesh() -> list:
    SOURCE   = "Nefesh B'Nefesh"
    BASE_URL = "https://www.nbn.org.il"
    jobs     = []
    driver   = None

    search_terms = [
        "customer success",
        "customer success manager",
        "\u05d4\u05e6\u05dc\u05d7\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",
        "\u05de\u05e0\u05d4\u05dc \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",
    ]

    try:
        driver = get_selenium_driver()
        for term in search_terms:
            encoded = requests.utils.quote(term)
            url = f"{BASE_URL}/jobboard/?search_keywords={encoded}&search_region=71"
            try:
                driver.get(url)
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "li.job_listing, div.job_listing, article")
                    )
                )
                time.sleep(2)
            except Exception:
                time.sleep(3)

            soup  = BeautifulSoup(driver.page_source, "html.parser")
            cards = soup.select("li.job_listing, div.job_listing, article.job_listing")

            for card in cards[:40]:
                try:
                    title_el   = card.select_one("h3.job_listing-title, h3, h2")
                    company_el = card.select_one(".job_listing-company strong, .company, [class*='company']")
                    link_el    = card.select_one("a.job_listing-clickbox, a[href*='/job/']")
                    date_el    = card.select_one("li.job_listing-date, .job_listing-date")

                    title   = title_el.get_text(strip=True)   if title_el   else ""
                    company = company_el.get_text(strip=True) if company_el else ""
                    href    = link_el["href"] if link_el and link_el.get("href") else card.get("data-href", "")
                    url_full    = href if href.startswith("http") else f"{BASE_URL}{href}"
                    posted_date = date_el.get_text(strip=True) if date_el else ""

                    if title and href:
                        jobs.append({
                            "title": title, "company": company,
                            "location": "Tel Aviv, Israel", "url": url_full,
                            "description": "", "source": SOURCE,
                            "posted_date": posted_date,
                        })
                except Exception:
                    continue
            time.sleep(REQUEST_DELAY)

    except Exception as e:
        print(f"  [{SOURCE}] Selenium error: {e}")
    finally:
        if driver:
            driver.quit()

    seen, unique = set(), []
    for j in jobs:
        k = make_job_id(j)
        if k not in seen:
            seen.add(k); unique.append(j)
    print(f"  {SOURCE}: {len(unique)} listings")
    return unique


# ── JobShop (Selenium) ───────────────────────────────────────────────────────

def fetch_jobshop() -> list:
    SOURCE   = "JobShop"
    BASE_URL = "https://jobshop.co.il"
    jobs     = []
    driver   = None

    search_terms = [
        "customer success",
        "customer success manager",
        "\u05d4\u05e6\u05dc\u05d7\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",
        "\u05de\u05e0\u05d4\u05dc \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",
        "\u05d7\u05d5\u05d5\u05d9\u05d9\u05ea \u05dc\u05e7\u05d5\u05d7",
    ]

    try:
        driver = get_selenium_driver()
        for term in search_terms:
            encoded = requests.utils.quote(term)
            url = f"{BASE_URL}/?s={encoded}"
            try:
                driver.get(url)
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "article"))
                )
                time.sleep(2)
            except Exception:
                time.sleep(3)

            soup  = BeautifulSoup(driver.page_source, "html.parser")
            cards = soup.select("article")

            for card in cards[:40]:
                try:
                    title_el   = card.select_one("h2, h3, h4, .elementor-post__title")
                    link_el    = card.select_one("a.elementor-post__read-more, h2 a, h3 a, a[href*='/jobs/']")
                    company_el = card.select_one("[class*='company'], [class*='employer']")

                    title    = title_el.get_text(strip=True)   if title_el   else ""
                    company  = company_el.get_text(strip=True) if company_el else ""
                    href     = link_el["href"] if link_el and link_el.get("href") else ""
                    url_full = href if href.startswith("http") else f"{BASE_URL}{href}"

                    if title and href:
                        jobs.append({
                            "title": title, "company": company,
                            "location": "Tel Aviv, Israel", "url": url_full,
                            "description": "", "source": SOURCE,
                        })
                except Exception:
                    continue
            time.sleep(REQUEST_DELAY)

    except Exception as e:
        print(f"  [{SOURCE}] Selenium error: {e}")
    finally:
        if driver:
            driver.quit()

    seen, unique = set(), []
    for j in jobs:
        k = make_job_id(j)
        if k not in seen:
            seen.add(k); unique.append(j)
    print(f"  {SOURCE}: {len(unique)} listings")
    return unique


# ── LinkedIn (best-effort) ────────────────────────────────────────────────────

def fetch_linkedin() -> list:
    SOURCE = "LinkedIn"
    jobs   = []

    queries = [
        "customer+success+manager",
        "head+of+customer+success",
        "VP+customer+success",
        "founding+customer+success",
        "%D7%94%D7%A6%D7%9C%D7%97%D7%AA+%D7%9C%D7%A7%D7%95%D7%97%D7%95%D7%AA",
        "%D7%9E%D7%A0%D7%94%D7%9C+%D7%9C%D7%A7%D7%95%D7%97%D7%95%D7%AA",
    ]

    for q in queries:
        url  = (
            f"https://www.linkedin.com/jobs/search/"
            f"?keywords={q}&location=Tel+Aviv%2C+Israel&sortBy=DD"
        )
        soup = safe_get(url, SOURCE)
        if not soup:
            continue

        cards = soup.select(
            "div.base-card, "
            "li.jobs-search-results__list-item, "
            "div[class*='job-search-card'], "
            "div[data-entity-urn]"
        )

        for card in cards[:20]:
            try:
                title_el   = card.select_one("h3.base-search-card__title, h3[class*='title'], span[class*='title']")
                company_el = card.select_one("h4.base-search-card__subtitle, a[class*='company'], span[class*='company']")
                link_el    = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")

                title   = title_el.get_text(strip=True)   if title_el   else ""
                company = company_el.get_text(strip=True) if company_el else ""
                href    = link_el["href"]                 if link_el    else ""

                if title and href:
                    jobs.append({
                        "title": title, "company": company,
                        "location": "Tel Aviv, Israel", "url": href,
                        "description": "", "source": SOURCE,
                    })
            except Exception:
                continue
        time.sleep(REQUEST_DELAY)

    print(f"  {SOURCE}: {len(jobs)} listings (best-effort)")
    return jobs


# ── Indeed IL (Selenium) ──────────────────────────────────────────────────────

def fetch_indeed() -> list:
    """
    Scrapes il.indeed.com for Customer Success roles in Tel Aviv.
    Searches English, French, and Hebrew query terms.
    Uses Selenium because Indeed is heavily JS-rendered.
    """
    SOURCE   = "Indeed IL"
    BASE_URL = "https://il.indeed.com"
    jobs     = []
    driver   = None

    # (query, language note)
    search_terms = [
        ("customer success manager",        "en"),
        ("customer success",                "en"),
        ("founding customer success",       "en"),
        ("head of customer success",        "en"),
        ("VP customer success",             "en"),
        ("responsable customer success",    "fr"),
        ("charge de succes client",         "fr"),
        ("\u05d4\u05e6\u05dc\u05d7\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",  "he"),
        ("\u05de\u05e0\u05d4\u05dc \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",         "he"),
        ("\u05d7\u05d5\u05d5\u05d9\u05d9\u05ea \u05dc\u05e7\u05d5\u05d7",          "he"),
    ]

    try:
        driver = get_selenium_driver()

        for term, _lang in search_terms:
            encoded = requests.utils.quote(term)
            url = f"{BASE_URL}/jobs?q={encoded}&l=Tel+Aviv"
            try:
                driver.get(url)
                WebDriverWait(driver, 12).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "div.job_seen_beacon, div.tapItem, li.css-5lfssm")
                    )
                )
                time.sleep(2)
            except Exception:
                time.sleep(3)

            soup = BeautifulSoup(driver.page_source, "html.parser")

            # Indeed rotates class names — cover the main variants
            cards = soup.select(
                "div.job_seen_beacon, "
                "div.tapItem, "
                "li.css-5lfssm, "
                "td.resultContent"
            )

            for card in cards[:30]:
                try:
                    # Title
                    title_el = card.select_one(
                        "h2.jobTitle span[title], "
                        "h2.jobTitle a span, "
                        "h2[class*='jobTitle'] span, "
                        "a.jcs-JobTitle span"
                    )
                    title = title_el.get_text(strip=True) if title_el else ""

                    # Company
                    company_el = card.select_one(
                        "span[data-testid='company-name'], "
                        "[class*='companyName'], "
                        "span.companyName"
                    )
                    company = company_el.get_text(strip=True) if company_el else ""

                    # Location
                    loc_el = card.select_one(
                        "div[data-testid='text-location'], "
                        "[class*='companyLocation']"
                    )
                    location = loc_el.get_text(strip=True) if loc_el else "Tel Aviv, Israel"

                    # Date posted
                    date_el = card.select_one(
                        "span[data-testid='myJobsStateDate'], span.date, span[class*='date']"
                    )
                    posted_date = date_el.get_text(strip=True) if date_el else ""

                    # URL
                    link_el = card.select_one("h2.jobTitle a, a.jcs-JobTitle")
                    href    = link_el["href"] if link_el and link_el.get("href") else ""
                    if href and not href.startswith("http"):
                        href = f"{BASE_URL}{href}"

                    if title and href:
                        jobs.append({
                            "title": title, "company": company,
                            "location": location or "Tel Aviv, Israel",
                            "url": href, "description": "",
                            "source": SOURCE, "posted_date": posted_date,
                        })
                except Exception:
                    continue

            time.sleep(REQUEST_DELAY)

    except Exception as e:
        print(f"  [{SOURCE}] Selenium error: {e}")
    finally:
        if driver:
            driver.quit()

    seen, unique = set(), []
    for j in jobs:
        k = make_job_id(j)
        if k not in seen:
            seen.add(k); unique.append(j)
    print(f"  {SOURCE}: {len(unique)} listings")
    return unique


# ── Aggregate ─────────────────────────────────────────────────────────────────

def fetch_all_sources() -> list:
    print("\nScraping sources:")
    all_jobs = []
    all_jobs += fetch_startup_for_startup()
    all_jobs += fetch_nefesh_bnefesh()
    all_jobs += fetch_jobshop()
    all_jobs += fetch_linkedin()
    all_jobs += fetch_indeed()
    return all_jobs


# =============================================================================
# 5. EMAIL DIGEST
# =============================================================================

SOURCE_COLORS = {
    "StartupForStartup": "#e0f2fe",
    "Nefesh B'Nefesh":   "#fce7f3",
    "JobShop":           "#fef9c3",
    "LinkedIn":          "#dbeafe",
    "Indeed IL":         "#dcfce7",
}


def build_email_html(new_jobs: list) -> str:
    today = datetime.date.today().strftime("%A, %d %B %Y")
    count = len(new_jobs)

    rows = ""
    for job in new_jobs:
        saas_badge = (
            '<span style="background:#d1fae5;color:#065f46;padding:2px 8px;'
            'border-radius:4px;font-size:11px;margin-left:8px;">&#10003; SaaS/Fintech</span>'
            if job.get("saas_signal") else
            '<span style="background:#fef3c7;color:#92400e;padding:2px 8px;'
            'border-radius:4px;font-size:11px;margin-left:8px;">? Verify company</span>'
        )
        source    = job.get("source", "")
        source_bg = SOURCE_COLORS.get(source, "#f3f4f6")
        desc      = job.get("description") or ""
        desc_html = (
            f'<div style="color:#777;font-size:12px;margin-top:6px;line-height:1.5;">'
            f'{desc[:200]}{"..." if len(desc) > 200 else ""}</div>'
        ) if desc else ""

        rows += f"""
        <tr>
          <td style="padding:14px 16px;border-bottom:1px solid #f0f0f0;">
            <div style="font-weight:600;font-size:15px;">
              <a href="{job['url']}" style="color:#1a1a1a;text-decoration:none;">
                {job['title']}
              </a>
              {saas_badge}
            </div>
            <div style="color:#555;font-size:13px;margin-top:5px;">
              {job.get('company') or '&mdash;'}
              &nbsp;&middot;&nbsp;
              {job.get('location') or 'Israel'}
              &nbsp;&middot;&nbsp;
              <span style="background:{source_bg};padding:1px 7px;border-radius:3px;
                           font-size:11px;color:#444;">{source}</span>
              {("&nbsp;&middot;&nbsp;<span style='color:#aaa;font-size:11px;'>" + job.get('posted_date','') + "</span>") if job.get('posted_date') else ""}
            </div>
            <div style="font-size:12px;color:#888;margin-top:3px;">
              <a href="{job['url']}" style="color:#888;">{job['url']}</a>
            </div>
            {desc_html}
          </td>
        </tr>"""

    body = (
        "<p style='color:#555;padding:20px 0;'>No new listings today. Check back tomorrow.</p>"
        if count == 0 else
        f'<table width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse;border:1px solid #e8e8e8;'
        f'border-radius:8px;overflow:hidden;">{rows}</table>'
    )

    legend = "".join(
        f'<span style="display:inline-block;margin:2px 4px;padding:2px 8px;'
        f'background:{bg};border-radius:3px;font-size:11px;color:#444;">{src}</span>'
        for src, bg in SOURCE_COLORS.items()
    )

    return f"""<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             max-width:680px;margin:0 auto;padding:24px;color:#1a1a1a;">
  <h2 style="font-size:20px;margin-bottom:4px;">
    &#127919; {count} new CS role{'s' if count != 1 else ''} &middot; {today}
  </h2>
  <p style="color:#888;font-size:12px;margin-top:0;margin-bottom:8px;">
    Filters: Tel Aviv &middot; B2B SaaS &middot; Fintech &middot; English / French / Hebrew &middot; Founding CSM
  </p>
  <p style="color:#888;font-size:12px;margin-top:0;margin-bottom:20px;">
    Sources: {legend}
  </p>
  {body}
  <p style="color:#ccc;font-size:11px;margin-top:24px;
            border-top:1px solid #f0f0f0;padding-top:12px;">
    Your job search bot &middot; runs weekdays at 10am Israel time
  </p>
</body>
</html>"""


def send_email(new_jobs: list):
    sender   = os.getenv("EMAIL_FROM")
    receiver = os.getenv("EMAIL_TO")
    password = os.getenv("EMAIL_PASSWORD")

    if not all([sender, receiver, password]):
        print("\n  [!] Email not configured — skipping send.")
        print("      Add EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD to your .env file.")
        return

    count    = len(new_jobs)
    date_str = datetime.date.today().strftime("%d %b")
    subject  = (
        f"\U0001f3af {count} new CSM role{'s' if count != 1 else ''} | Tel Aviv | {date_str}"
        if count > 0 else
        f"Job bot | no new roles today | {date_str}"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = receiver
    msg.attach(MIMEText(build_email_html(new_jobs), "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        print(f"\n  [ok] Email sent — {count} new role{'s' if count != 1 else ''} to {receiver}")
    except smtplib.SMTPAuthenticationError:
        print("\n  [!] Email auth failed — check your Gmail App Password in .env")
    except smtplib.SMTPException as e:
        print(f"\n  [!] Email error: {e}")


# =============================================================================
# 6. MAIN PIPELINE
# =============================================================================

def main():
    print(f"\n{'='*60}")
    print(f"Job Search Bot | {datetime.datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*60}")

    seen_ids = load_cache()
    print(f"\nCache: {len(seen_ids)} previously seen listings")

    raw_jobs = fetch_all_sources()
    print(f"\nRaw total:     {len(raw_jobs)}")

    filtered = [j for j in raw_jobs if passes_filters(j)]
    print(f"After filters: {len(filtered)}")

    new_jobs, new_ids = [], set()
    for job in filtered:
        jid = make_job_id(job)
        if jid not in seen_ids:
            new_jobs.append(job)
            new_ids.add(jid)

    print(f"New today:     {len(new_jobs)}")

    seen_ids.update(new_ids)
    save_cache(seen_ids)

    send_email(new_jobs)

    if new_jobs:
        print("\nNew listings:")
        for job in new_jobs:
            flag = "ok" if job.get("saas_signal") else "?"
            print(f"  [{flag}] [{job['source']:<20}] {job['title']} | {job.get('company', '')}")
    else:
        print("\nNo new listings today.")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()


# =============================================================================
# SCHEDULING — GitHub Actions (completely free, no server needed)
# =============================================================================
#
# Step 1: Create a PRIVATE repo on github.com and push this file to it.
#         Also push a .gitignore containing:
#           .env
#           seen_jobs_cache.json
#
# Step 2: Settings > Secrets and variables > Actions > New repository secret
#         Add these 3 secrets:
#           EMAIL_FROM      your Gmail address
#           EMAIL_TO        your Gmail address
#           EMAIL_PASSWORD  your 16-char Gmail App Password
#
# Step 3: Create .github/workflows/job_bot.yml with the content below.
#
# -----------------------------------------------------------------------
# name: Job Search Bot
# on:
#   schedule:
#     - cron: '0 7 * * 1-5'   # 10am Israel time (IDT = UTC+3), Mon-Fri
#                               # In winter (IST = UTC+2): change to '0 8 * * 1-5'
#   workflow_dispatch:          # lets you trigger manually from GitHub UI
# jobs:
#   run:
#     runs-on: ubuntu-latest
#     steps:
#       - uses: actions/checkout@v4
#       - uses: actions/setup-python@v5
#         with:
#           python-version: '3.11'
#       - name: Restore seen-jobs cache
#         uses: actions/cache@v4
#         with:
#           path: seen_jobs_cache.json
#           key: job-cache-${{ github.run_id }}
#           restore-keys: job-cache-
#       - name: Install dependencies
#         run: pip install requests beautifulsoup4 python-dotenv selenium webdriver-manager
#       - name: Run bot
#         run: python job_search_automation.py
#         env:
#           EMAIL_FROM:     ${{ secrets.EMAIL_FROM }}
#           EMAIL_TO:       ${{ secrets.EMAIL_TO }}
#           EMAIL_PASSWORD: ${{ secrets.EMAIL_PASSWORD }}
# -----------------------------------------------------------------------
#
# =============================================================================
# TROUBLESHOOTING
# =============================================================================
#
# "0 listings from [source]"
#   The site updated its HTML. Open in Chrome, right-click a job card >
#   Inspect, find the card CSS class, update the selector in the scraper.
#
# "LinkedIn returns 0"
#   LinkedIn served a CAPTCHA. Expected occasionally. The other sources
#   still ran fine — nothing to fix, retry tomorrow.
#
# "Indeed returns 0"
#   Indeed rotates class names periodically. Inspect a card on
#   il.indeed.com and update the selectors in fetch_indeed().
#
# "Email auth failed"
#   Use a Gmail App Password (16 chars), NOT your regular Gmail password.
#   Enable 2FA first, then: myaccount.google.com/apppasswords
