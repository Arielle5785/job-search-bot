"""
Job Search Automation — Multi-User, AI-Powered Title Variants
=============================================================
100% free. No RSS. No Apify. No paid APIs.

Sources scraped (BeautifulSoup + requests / Selenium):
  - StartupForStartup  (startupforstartup.com)
  - Nefesh B'Nefesh    (nbn.org.il)
  - JobShop            (jobshop.co.il)
  - LinkedIn           (best-effort, graceful fail if blocked)
  - Indeed IL          (il.indeed.com)

MULTI-USER SETUP
  Add USERS_JSON to GitHub Secrets (Settings > Secrets > Actions):
  [
    {
      "name": "Sara Cohen",
      "email": "sara@gmail.com",
      "profession": "data analyst",
      "location": ["tel aviv", "remote"],
      "languages": ["english", "hebrew"]
    }
  ]

OTHER SECRETS NEEDED
  EMAIL_FROM       your Gmail address
  EMAIL_PASSWORD   your 16-char Gmail App Password (no spaces)
  ANTHROPIC_API_KEY  your Anthropic API key

ONE-TIME SETUP
  pip install requests beautifulsoup4 python-dotenv selenium webdriver-manager anthropic
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
import anthropic

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
# 1. LOAD USERS — Neon DB (primary) → USERS_JSON (fallback)
# =============================================================================

def load_users() -> list:
    """
    Load users from Neon Postgres via v_users view.
    Falls back to USERS_JSON secret for backwards compatibility.
    """
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        try:
            import psycopg2
            import psycopg2.extras
            conn = psycopg2.connect(database_url, cursor_factory=psycopg2.extras.RealDictCursor)
            cur = conn.cursor()
            cur.execute("SELECT * FROM v_users WHERE is_active = TRUE")
            rows = cur.fetchall()
            cur.close()
            conn.close()

            if rows:
                users = []
                for row in rows:
                    users.append({
                        "name": row["full_name"],
                        "email": row["email"],
                        "profession": row["profession"] or "",
                        "variants": list(row["variants"] or []),
                        "seniority": list(row["seniority"] or []),
                        "company_type": list(row["company_type"] or []),
                        "city": list(row["city"] or []),
                        "work_type": row["work_type"] or "Any",
                        "websites": list(row["websites"] or []),
                        "frequency": list(row["frequency"] or ["08:00"]),
                    })
                print(f"\nLoaded {len(users)} user(s) from Neon DB")
                return users
            else:
                print("  [!] Neon DB is empty — falling back to USERS_JSON")

        except Exception as e:
            print(f"  [!] Neon DB error: {e} — falling back to USERS_JSON")

    # Fallback: USERS_JSON secret
    users_json = os.getenv("USERS_JSON")
    if users_json:
        try:
            users = json.loads(users_json)
            print(f"\nLoaded {len(users)} user(s) from USERS_JSON (fallback)")
            return users
        except json.JSONDecodeError:
            print("  [!] USERS_JSON is invalid JSON")

    print("  [!] No users found — set DATABASE_URL or USERS_JSON secret")
    return []


# =============================================================================
# 2. CLAUDE AI — GENERATE TITLE VARIANTS
# =============================================================================

def generate_title_variants(profession: str, user_variants: list) -> list:
    """
    Use user-provided variants if available, else call Claude AI to generate them.
    Returns a list of title strings.
    """
    # If user typed their own variants, use those + the main profession
    if user_variants:
        all_variants = [profession] + user_variants
        cleaned = [v.strip() for v in all_variants if v.strip()]
        print(f"\n  [Variants] Using {len(cleaned)} user-provided variants")
        return cleaned

    print(f"\n  [Claude] Auto-generating title variants for: {profession}")

    prompt = f"""You are helping a job search bot find relevant job listings in Israel.

Generate a comprehensive list of job title variants for someone whose profession is: "{profession}"

Requirements:
- Include seniority variants: manager, lead, director, head of, VP, chief
- Include founding/first-hire variants if relevant
- Include adjacent titles commonly used in Israeli B2B SaaS and Fintech companies
- Include titles in English and Hebrew (use actual Hebrew characters)
- All English titles must be lowercase
- Return ONLY a JSON array of strings, nothing else, no markdown, no explanation

Example format:
["title one", "title two", "כותרת בעברית"]"""

    try:
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()

        # Strip markdown code fences if present
        raw = re.sub(r"```json|```", "", raw).strip()

        variants = json.loads(raw)
        if isinstance(variants, list):
            # Normalise: lowercase for non-Hebrew/non-French
            cleaned = [v.strip() for v in variants if isinstance(v, str) and v.strip()]
            print(f"  [Claude] Generated {len(cleaned)} variants")
            return cleaned
    except Exception as e:
        print(f"  [Claude] Error generating variants: {e}")

    # Fallback — basic variants from profession string
    base = profession.lower().strip()
    return [
        base,
        f"senior {base}",
        f"lead {base}",
        f"head of {base.replace('manager', '').strip()}",
        f"director of {base.replace('manager', '').strip()}",
        f"vp {base.replace('manager', '').strip()}",
    ]


# =============================================================================
# 3. FILTER LOGIC
# =============================================================================

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


def passes_filters(job: dict, title_variants: list, user: dict) -> bool:
    """
    Returns True if the job passes all gates for a specific user.
    Uses per-user title_variants, city, work_type, seniority, company_type.
    """
    title       = (job.get("title", "") or "").lower()
    location    = (job.get("location", "") or "").lower()
    description = (job.get("description", "") or "").lower()
    company     = (job.get("company", "") or "").lower()
    source      = (job.get("source", "") or "").lower()

    full_text = f"{title} {location} {description} {company}"

    # Normalise title for matching
    normalised_title = re.sub(r"[^\w\s]", " ", title)
    normalised_title = re.sub(r"\s+", " ", normalised_title).strip()

    # Gate 0 — skip jobs older than ~1 month
    posted_date = (job.get("posted_date") or "").lower()
    old_signals = ["month", "months", "30+ days", "30 days ago", "מזמן"]
    if any(s in posted_date for s in old_signals):
        return False
    
    # Gate 1 — title must match one of the user's variants
    if not any(v.lower() in normalised_title for v in title_variants):
        return False

    # Gate 2 — city/location (skip for Israel-native boards)
    cities     = [c.lower() for c in user.get("city", [])]
    work_type  = (user.get("work_type") or "any").lower()
    if cities and "no preference" not in cities:
        if not any(s in source for s in ISRAEL_NATIVE_SOURCES):
            if not any(c in full_text for c in cities):
                return False

    # Gate 3 — work type filter
    if work_type not in ("any", ""):
        remote_terms  = ["remote", "מרחוק"]
        hybrid_terms  = ["hybrid", "היברידי"]
        onsite_terms  = ["on-site", "onsite", "office", "on site"]
        if work_type == "remote" and not any(t in full_text for t in remote_terms):
            pass  # soft signal only — don't hard-exclude
        if work_type == "on-site" and any(t in full_text for t in remote_terms):
            return False  # user wants on-site, skip remote roles

    # Gate 4 — company type filter
    company_types = [ct.lower() for ct in user.get("company_type", [])]
    if company_types and "b2c" not in company_types:
        # If user did NOT select B2C, exclude known B2C signals
        b2c_signals = ["b2c", "e-commerce", "ecommerce", "retail", "gaming", "game"]
        if any(s in full_text for s in b2c_signals):
            return False
    if company_types and "b2b" not in company_types:
        # If user did NOT select B2B, skip pure B2B signals
        pass  # B2B is hard to detect reliably — leave as soft

    # Gate 5 — seniority filter (soft — badge only, no hard exclusion)
    seniority_levels = [s.lower() for s in user.get("seniority", [])]
    if "junior" not in seniority_levels:
        junior_signals = ["junior", "entry level", "entry-level", "graduate", "intern"]
        if any(s in full_text for s in junior_signals):
            return False

    # Gate 6 — hard exclusions regardless of user prefs
    HARD_EXCLUDE = ["internship", "intern", "student", "apprentice", "bpo", "call center"]
    if any(kw in full_text for kw in HARD_EXCLUDE):
        return False

    # Soft badge — SaaS/Fintech signal
    saas_signals = ["saas", "b2b", "fintech", "software", "platform", "startup",
                    "series a", "series b", "seed", "venture"]
    job["saas_signal"] = any(kw in full_text for kw in saas_signals)

    return True


# =============================================================================
# 4. DEDUPLICATION CACHE (per user)
# =============================================================================

def get_cache_file(user_email: str) -> str:
    """Return a cache file path unique to each user."""
    safe_email = re.sub(r"[^\w]", "_", user_email)
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"seen_jobs_cache_{safe_email}.json"
    )


def load_cache(user_email: str) -> set:
    cache_file = get_cache_file(user_email)
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
        except (json.JSONDecodeError, IOError):
            print(f"  [!] Cache corrupted for {user_email} — starting fresh")
    return set()


def save_cache(user_email: str, seen_ids: set):
    cache_file = get_cache_file(user_email)
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen_ids)), f, indent=2)
    except IOError as e:
        print(f"  [!] Could not save cache for {user_email}: {e}")


def make_job_id(job: dict) -> str:
    company = re.sub(r"\s+", " ", (job.get("company") or "").lower().strip())
    title   = re.sub(r"\s+", " ", (job.get("title")   or "").lower().strip())
    return hashlib.md5(f"{company}|{title}".encode()).hexdigest()


# =============================================================================
# 5. SCRAPERS (unchanged from original — search terms now dynamic)
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


def build_search_terms(profession: str, languages: list) -> list:
    """
    Build a short list of core search terms to send to job boards.
    We keep this short (3-5 terms) to avoid hammering servers.
    The full filtering happens via title_variants after scraping.
    """
    terms = []
    base = profession.lower().strip()

    # Always include the raw profession as typed
    terms.append(base)

    # Add a simplified version if multi-word
    words = base.split()
    if len(words) > 2:
        terms.append(" ".join(words[:2]))

    # Hebrew if requested
    if "hebrew" in languages:
        # Generic Hebrew search terms — covers most professions
        terms.append("מנהל")   # manager
        terms.append("מנהלת")  # manager (f)

    return terms[:5]  # cap at 5 to be polite


# ── StartupForStartup (Selenium) ──────────────────────────────────────────────

def fetch_startup_for_startup(search_terms: list) -> list:
    SOURCE   = "StartupForStartup"
    BASE_URL = "https://www.startupforstartup.com"
    jobs     = []
    driver   = None

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

def fetch_nefesh_bnefesh(search_terms: list) -> list:
    SOURCE   = "Nefesh B'Nefesh"
    BASE_URL = "https://www.nbn.org.il"
    jobs     = []
    driver   = None

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

def fetch_jobshop(search_terms: list) -> list:
    SOURCE   = "JobShop"
    BASE_URL = "https://jobshop.co.il"
    jobs     = []
    driver   = None

    try:
        driver = get_selenium_driver()
        for term in search_terms:
            encoded = requests.utils.quote(term)
            url = f"{BASE_URL}/?s={encoded}"
            try:
                driver.get(url)
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "article, div.job-listing, div[class*='job']")
                    )
                )
                time.sleep(2)
            except Exception:
                time.sleep(3)

            soup  = BeautifulSoup(driver.page_source, "html.parser")
            cards = soup.select("article, div.job-listing")

            for card in cards[:40]:
                try:
                    title_el   = card.select_one("h2, h3, .entry-title")
                    company_el = card.select_one(".company, [class*='company']")
                    link_el    = card.select_one("a[href*='jobshop']")

                    title   = title_el.get_text(strip=True)   if title_el   else ""
                    company = company_el.get_text(strip=True) if company_el else ""
                    href    = link_el["href"] if link_el and link_el.get("href") else ""

                    if title and href:
                        jobs.append({
                            "title": title, "company": company,
                            "location": "Tel Aviv, Israel", "url": href,
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

def fetch_linkedin(search_terms: list) -> list:
    SOURCE = "LinkedIn"
    jobs   = []

    for term in search_terms[:3]:  # cap LinkedIn queries
        encoded = requests.utils.quote(term)
        url = (
            f"https://www.linkedin.com/jobs/search/"
            f"?keywords={encoded}&location=Tel+Aviv%2C+Israel&sortBy=DD"
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

def fetch_indeed(search_terms: list) -> list:
    SOURCE   = "Indeed IL"
    BASE_URL = "https://il.indeed.com"
    jobs     = []
    driver   = None

    try:
        driver = get_selenium_driver()

        for term in search_terms:
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
            cards = soup.select(
                "div.job_seen_beacon, "
                "div.tapItem, "
                "li.css-5lfssm, "
                "td.resultContent"
            )

            for card in cards[:30]:
                try:
                    title_el = card.select_one(
                        "h2.jobTitle span[title], "
                        "h2.jobTitle a span, "
                        "h2[class*='jobTitle'] span, "
                        "a.jcs-JobTitle span"
                    )
                    title = title_el.get_text(strip=True) if title_el else ""

                    company_el = card.select_one(
                        "span[data-testid='company-name'], "
                        "[class*='companyName'], "
                        "span.companyName"
                    )
                    company = company_el.get_text(strip=True) if company_el else ""

                    loc_el = card.select_one(
                        "div[data-testid='text-location'], "
                        "[class*='companyLocation']"
                    )
                    location = loc_el.get_text(strip=True) if loc_el else "Tel Aviv, Israel"

                    date_el = card.select_one(
                        "span[data-testid='myJobsStateDate'], span.date, span[class*='date']"
                    )
                    posted_date = date_el.get_text(strip=True) if date_el else ""

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

def fetch_all_sources(search_terms: list) -> list:
    print("\nScraping sources:")
    all_jobs = []
    all_jobs += fetch_startup_for_startup(search_terms)
    all_jobs += fetch_nefesh_bnefesh(search_terms)
    all_jobs += fetch_jobshop(search_terms)
    all_jobs += fetch_linkedin(search_terms)
    all_jobs += fetch_indeed(search_terms)
    return all_jobs


# =============================================================================
# 6. EMAIL DIGEST
# =============================================================================

SOURCE_COLORS = {
    "StartupForStartup": "#e0f2fe",
    "Nefesh B'Nefesh":   "#fce7f3",
    "JobShop":           "#fef9c3",
    "LinkedIn":          "#dbeafe",
    "Indeed IL":         "#dcfce7",
}


def build_email_html(new_jobs: list, user: dict) -> str:
    today      = datetime.date.today().strftime("%A, %d %B %Y")
    count      = len(new_jobs)
    name       = user.get("name", "").split()[0]  # first name only
    profession = user.get("profession", "roles")
    cities     = user.get("city", [])
    location   = ", ".join(cities).title() if cities else "Israel"
    work_type  = user.get("work_type", "")
    seniority  = ", ".join(user.get("seniority", []))
    co_type    = ", ".join(user.get("company_type", []))

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

    greeting = f"Hi {name}," if name else "Hi,"

    return f"""<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             max-width:680px;margin:0 auto;padding:24px;color:#1a1a1a;">
  <p style="color:#555;font-size:14px;margin-bottom:16px;">{greeting}</p>
  <h2 style="font-size:20px;margin-bottom:4px;">
    &#127919; {count} new {profession} role{'s' if count != 1 else ''} &middot; {today}
  </h2>
  <p style="color:#888;font-size:12px;margin-top:0;margin-bottom:8px;">
    Location: {location} {("&middot; " + work_type) if work_type else ""} &middot; {co_type or "B2B/B2C"} &middot; {seniority or "All levels"}
  </p>
  <p style="color:#888;font-size:12px;margin-top:0;margin-bottom:20px;">
    Sources: {legend}
  </p>
  {body}
  <p style="color:#ccc;font-size:11px;margin-top:24px;
            border-top:1px solid #f0f0f0;padding-top:12px;">
    Your job search bot &middot; runs weekdays at 10am Israel time
    &middot; Reply to unsubscribe
  </p>
</body>
</html>"""


def send_email(new_jobs: list, user: dict):
    sender   = os.getenv("EMAIL_FROM")
    password = os.getenv("EMAIL_PASSWORD")
    receiver = user.get("email")

    if not all([sender, receiver, password]):
        print(f"\n  [!] Email not configured — skipping send for {receiver}")
        return

    count      = len(new_jobs)
    profession = user.get("profession", "roles")
    date_str   = datetime.date.today().strftime("%d %b")
    subject    = (
        f"\U0001f3af {count} new {profession} role{'s' if count != 1 else ''} | {date_str}"
        if count > 0 else
        f"Job bot | no new {profession} roles today | {date_str}"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = receiver
    msg.attach(MIMEText(build_email_html(new_jobs, user), "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        print(f"  [ok] Email sent — {count} new role{'s' if count != 1 else ''} to {receiver}")
    except smtplib.SMTPAuthenticationError:
        print("\n  [!] Email auth failed — check your Gmail App Password in .env")
    except smtplib.SMTPException as e:
        print(f"\n  [!] Email error: {e}")


# =============================================================================
# 7. MAIN PIPELINE
# =============================================================================

def main():
    print(f"\n{'='*60}")
    print(f"Job Search Bot | {datetime.datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*60}")

    users = load_users()
    if not users:
        print("No users to process. Exiting.")
        return
    is_manual = os.getenv("IS_MANUAL", "false").lower() == "true"

    # Scrape all sources ONCE — then filter per user
    # Build search terms from ALL users so every profession gets scraped
    search_terms = []
    for u in users:
        terms = build_search_terms(u.get("profession", ""), ["english"])
        for t in terms:
            if t not in search_terms:
                search_terms.append(t)

    print(f"\nSearch terms: {search_terms}")
    raw_jobs = fetch_all_sources(search_terms)
    print(f"\nRaw total: {len(raw_jobs)} listings scraped")

    # Current Israel time (IDT = UTC+3)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_israel = now_utc + datetime.timedelta(hours=3)
    israel_time = now_israel.strftime("%H:%M")
    israel_total_minutes = now_israel.hour * 60 + now_israel.minute
    print(f"\nIsrael time: {israel_time}")

    # Process each user
    for user in users:
        name       = user.get("name", user.get("email", "unknown"))
        profession = user.get("profession", "")
        frequency  = user.get("frequency", ["08:00", "12:00", "17:00", "21:00"])
        variants   = user.get("variants", [])

        print(f"\n{'─'*60}")
        print(f"Processing: {name} ({profession})")

        # Frequency check — only run for this user at their chosen times
        if isinstance(frequency, list):
            freq_list = frequency
        else:
            freq_list = [f.strip() for f in str(frequency).split(",")]

        def is_scheduled_now(freq_list, current_total_minutes, tolerance=45):
            for t in freq_list:
                try:
                    h, m = map(int, t.strip().split(":"))
                    sched_total = h * 60 + m
                    if abs(current_total_minutes - sched_total) <= tolerance:
                        return True
                except ValueError:
                    continue
            return False

        if not is_manual and not is_scheduled_now(freq_list, israel_total_minutes):
            print(f"  [skip] {name} — not scheduled at {israel_time} (scheduled: {freq_list})")
            continue

        # Generate or use title variants
        title_variants = generate_title_variants(profession, variants)

        # Filter jobs for this user
        filtered = [j for j in raw_jobs if passes_filters(j, title_variants, user)]
        print(f"Matching jobs after filter: {len(filtered)}")

        # Deduplicate against this user's cache
        seen_ids = load_cache(user["email"])
        new_jobs, new_ids = [], set()
        for job in filtered:
            jid = make_job_id(job)
            if jid not in seen_ids:
                new_jobs.append(job)
                new_ids.add(jid)

        print(f"New today for {name}: {len(new_jobs)}")

        # Update cache
        seen_ids.update(new_ids)
        save_cache(user["email"], seen_ids)

        # Send email
        send_email(new_jobs, user)

    print(f"\n{'='*60}")
    print("All users processed.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()


# =============================================================================
# GITHUB ACTIONS WORKFLOW — update your job_bot.yml to add new secrets
# =============================================================================
#
# name: Job Search Bot
# on:
#   schedule:
#     - cron: '0 7 * * 1-5'   # 10am Israel time (IDT = UTC+3), Mon-Fri
#   workflow_dispatch:
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
#           path: seen_jobs_cache_*.json
#           key: job-cache-${{ github.run_id }}
#           restore-keys: job-cache-
#       - name: Install dependencies
#         run: pip install requests beautifulsoup4 python-dotenv selenium webdriver-manager anthropic
#       - name: Run bot
#         run: python job_search_automation.py
#         env:
#           EMAIL_FROM:        ${{ secrets.EMAIL_FROM }}
#           EMAIL_PASSWORD:    ${{ secrets.EMAIL_PASSWORD }}
#           ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
#           USERS_JSON:        ${{ secrets.USERS_JSON }}
# =============================================================================
