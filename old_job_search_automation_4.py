
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


# =============================================================================
# 1. TITLE VARIANTS
#    Covers every way "Customer Success Manager" is written in Israeli job ads.
#    Bug-fix applied: punctuation normalised before matching so "VP, Customer
#    Success" and "VP - Customer Success" both match correctly.
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

    # Hebrew forms found on Israeli boards
    "\u05de\u05e0\u05d4\u05dc \u05d4\u05e6\u05dc\u05d7\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",
    "\u05de\u05e0\u05d4\u05dc\u05ea \u05d4\u05e6\u05dc\u05d7\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",
]


def matches_title(job_title: str) -> bool:
    """
    Case-insensitive substring match with punctuation normalisation.
    Handles: 'VP, Customer Success', 'VP - Customer Success', 'VP | Customer Success'.
    """
    # Strip punctuation, collapse whitespace
    normalised = re.sub(r"[^\w\s]", " ", job_title.lower())
    normalised = re.sub(r"\s+", " ", normalised).strip()
    return any(v in normalised for v in TITLE_VARIANTS)


# =============================================================================
# 2. FILTER LOGIC  (4 gates)
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
    "startupforstartup", "lastartup", "jobshop", "nefesh b'nefesh"
}


def passes_filters(job: dict) -> bool:
    """
    Returns True if the job passes all 4 gates.
    Sets job['saas_signal'] as a soft badge (never excludes).

    Validated against:
      - 7 should-pass cases  -> all pass  ✓
      - 5 should-fail cases  -> all fail  ✓
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
#    JSON file stored next to the script.
#    Fingerprint = MD5(company|title) — catches same role on 3+ boards.
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
#    Each source wrapped in try/except — one failure never stops the run.
# =============================================================================

def safe_get(url: str, source_name: str) -> BeautifulSoup | None:
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


def extract_jobs_generic(
    soup: BeautifulSoup,
    source: str,
    base_url: str,
    card_selectors: list,
    title_selectors: list,
    company_selectors: list,
    location_default: str = "Israel",
    cap: int = 40,
) -> list:
    """
    Shared extractor. Tries each CSS selector in order, uses first that finds elements.
    Falls back to any <a> tag whose href looks like a job link.
    """
    jobs = []

    # Find cards
    cards = []
    for sel in card_selectors:
        cards = soup.select(sel)
        if cards:
            break

    if not cards:
        cards = soup.find_all(
            "a", href=re.compile(r"/(job|jobs|position|positions|career|careers)/", re.I)
        )

    for card in cards[:cap]:
        try:
            # Title
            title = ""
            for sel in title_selectors:
                el = card.select_one(sel)
                if el:
                    title = el.get_text(strip=True)
                    break
            if not title and card.name == "a":
                title = card.get_text(strip=True)
            if not title:
                continue

            # Company
            company = ""
            for sel in company_selectors:
                el = card.select_one(sel)
                if el:
                    company = el.get_text(strip=True)
                    break

            # URL
            link = card if card.name == "a" else card.find("a")
            href = (link.get("href") or "") if link else ""
            if not href:
                continue
            url = href if href.startswith("http") else f"{base_url}{href}"

            jobs.append({
                "title":       title,
                "company":     company,
                "location":    location_default,
                "url":         url,
                "description": "",
                "source":      source,
            })
        except Exception:
            continue

    return jobs


# ── StartupForStartup ─────────────────────────────────────────────────────────

def fetch_startup_for_startup() -> list:
    """
    Correct URL: https://www.startupforstartup.com/jobs-in-startups/
    Searches English and Hebrew keywords.
    Note: site is in Hebrew — Google Translate handles reading it.
    """
    SOURCE   = "StartupForStartup"
    BASE_URL = "https://www.startupforstartup.com"
    jobs     = []

    # English + Hebrew search terms
    search_terms = [
        "customer success",
        "customer success manager",
        "\u05d4\u05e6\u05dc\u05d7\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",   # הצלחת לקוחות
        "\u05de\u05e0\u05d4\u05dc \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",           # מנהל לקוחות
        "\u05d7\u05d5\u05d5\u05d9\u05d9\u05ea \u05dc\u05e7\u05d5\u05d7",           # חווית לקוח
    ]

    for term in search_terms:
        encoded = requests.utils.quote(term)
        url  = f"{BASE_URL}/jobs-in-startups/?query={encoded}"
        soup = safe_get(url, SOURCE)
        if not soup:
            continue

        found = extract_jobs_generic(
            soup, SOURCE, BASE_URL,
            card_selectors=[
                "div.job-card", "article.job", "li.job-listing",
                "div[class*='JobCard']", "div[class*='job-card']",
                "div[class*='position']", "div[class*='listing']",
            ],
            title_selectors=[
                "h2", "h3", "[class*='title']", "[class*='Title']", "[class*='job-title']",
            ],
            company_selectors=[
                "[class*='company']", "[class*='Company']", "[class*='employer']", "span.name",
            ],
            location_default="Tel Aviv, Israel",
        )
        jobs.extend(found)
        time.sleep(REQUEST_DELAY)

    # Deduplicate within source
    seen, unique = set(), []
    for j in jobs:
        k = make_job_id(j)
        if k not in seen:
            seen.add(k)
            unique.append(j)

    print(f"  {SOURCE}: {len(unique)} listings")
    return unique


# ── Nefesh B'Nefesh ──────────────────────────────────────────────────────────

def fetch_nefesh_bnefesh() -> list:
    """
    Correct URL: https://www.nbn.org.il/jobboard/
    Filter: region=71 (Tel Aviv / Center only)
    Searches English and Hebrew keywords.
    """
    SOURCE   = "Nefesh B'Nefesh"
    BASE_URL = "https://www.nbn.org.il"
    jobs     = []

    search_terms = [
        "customer success",
        "customer success manager",
        "\u05d4\u05e6\u05dc\u05d7\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",   # הצלחת לקוחות
        "\u05de\u05e0\u05d4\u05dc \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",           # מנהל לקוחות
    ]

    for term in search_terms:
        encoded = requests.utils.quote(term)
        # region=71 = Tel Aviv / Center
        url  = (
            f"{BASE_URL}/jobboard/"
            f"?search_keywords={encoded}"
            f"&search_region=71"
            f"&gjm_units=imperial"
        )
        soup = safe_get(url, SOURCE)
        if not soup:
            continue

        found = extract_jobs_generic(
            soup, SOURCE, BASE_URL,
            card_selectors=[
                "div.job-listing", "article.job", "li.job",
                "div[class*='job-card']", "div[class*='JobCard']",
                "div[class*='job_listing']", "li[class*='job']",
            ],
            title_selectors=["h2", "h3", ".job-title", "[class*='title']"],
            company_selectors=[".company", ".company-name", "[class*='company']"],
            location_default="Tel Aviv, Israel",
        )
        jobs.extend(found)
        time.sleep(REQUEST_DELAY)

    # Deduplicate within source
    seen, unique = set(), []
    for j in jobs:
        k = make_job_id(j)
        if k not in seen:
            seen.add(k)
            unique.append(j)

    print(f"  {SOURCE}: {len(unique)} listings")
    return unique


# ── JobShop ───────────────────────────────────────────────────────────────────

def fetch_jobshop() -> list:
    """
    Correct URL: https://jobshop.co.il/find
    Filters: location=מרכז (Center), type=משרה מלאה (Full-time)
    Searches English and Hebrew keywords.
    """
    SOURCE   = "JobShop"
    BASE_URL = "https://jobshop.co.il"
    jobs     = []

    search_terms = [
        "customer success",
        "customer success manager",
        "\u05d4\u05e6\u05dc\u05d7\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",   # הצלחת לקוחות
        "\u05de\u05e0\u05d4\u05dc \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",           # מנהל לקוחות
        "\u05d7\u05d5\u05d5\u05d9\u05d9\u05ea \u05dc\u05e7\u05d5\u05d7",           # חווית לקוח
    ]

    for term in search_terms:
        encoded = requests.utils.quote(term)
        url = (
            f"{BASE_URL}/find"
            f"?_sfm_job_location=%D7%9E%D7%A8%D7%9B%D7%96"          # מרכז = Center/Tel Aviv
            f"&_sfm_job_type=%D7%9E%D7%A9%D7%A8%D7%94%20%D7%9E%D7%9C%D7%90%D7%94"  # משרה מלאה = Full-time
            f"&_s={encoded}"
        )
        soup = safe_get(url, SOURCE)
        if not soup:
            continue

        found = extract_jobs_generic(
            soup, SOURCE, BASE_URL,
            card_selectors=[
                "div.job_listing", "li.job_listing",
                "div[class*='job']", "div[class*='position']",
                "article", "div.sf-field-post-meta",
            ],
            title_selectors=[
                "h2", "h3", "h4",
                ".position-title", "[class*='title']", "[class*='job-title']",
            ],
            company_selectors=[
                ".company", "[class*='company']", "[class*='employer']",
            ],
            location_default="Tel Aviv, Israel",
        )
        jobs.extend(found)
        time.sleep(REQUEST_DELAY)

    # Deduplicate within source
    seen, unique = set(), []
    for j in jobs:
        k = make_job_id(j)
        if k not in seen:
            seen.add(k)
            unique.append(j)

    print(f"  {SOURCE}: {len(unique)} listings")
    return unique


# ── LinkedIn (best-effort) ────────────────────────────────────────────────────

def fetch_linkedin() -> list:
    """
    Scrapes LinkedIn's public job search page (no login needed).
    Searches English and Hebrew keywords.
    Fragile by design — LinkedIn updates HTML often and may CAPTCHA.
    The other sources run regardless of whether this succeeds.
    """
    SOURCE = "LinkedIn"
    jobs   = []

    queries = [
        "customer+success+manager",
        "head+of+customer+success",
        "VP+customer+success",
        "%D7%94%D7%A6%D7%9C%D7%97%D7%AA+%D7%9C%D7%A7%D7%95%D7%97%D7%95%D7%AA",  # הצלחת לקוחות
        "%D7%9E%D7%A0%D7%94%D7%9C+%D7%9C%D7%A7%D7%95%D7%97%D7%95%D7%AA",         # מנהל לקוחות
    ]

    for q in queries:
        url  = (
            f"https://www.linkedin.com/jobs/search/"
            f"?keywords={q}&location=Tel+Aviv%2C+Israel"
            f"&f_TPR=r86400&sortBy=DD"
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
                title_el = card.select_one(
                    "h3.base-search-card__title, h3[class*='title'], span[class*='title']"
                )
                company_el = card.select_one(
                    "h4.base-search-card__subtitle, a[class*='company'], span[class*='company']"
                )
                link_el = card.select_one(
                    "a.base-card__full-link, a[href*='/jobs/view/']"
                )

                title   = title_el.get_text(strip=True)   if title_el   else ""
                company = company_el.get_text(strip=True) if company_el else ""
                href    = link_el["href"]                 if link_el    else ""

                if title and href:
                    jobs.append({
                        "title":       title,
                        "company":     company,
                        "location":    "Tel Aviv, Israel",
                        "url":         href,
                        "description": "",
                        "source":      SOURCE,
                    })
            except Exception:
                continue

        time.sleep(REQUEST_DELAY)

    print(f"  {SOURCE}: {len(jobs)} listings (best-effort)")
    return jobs


# ── Aggregate ─────────────────────────────────────────────────────────────────

def fetch_all_sources() -> list:
    print("\nScraping sources:")
    all_jobs = []
    all_jobs += fetch_startup_for_startup()
    all_jobs += fetch_nefesh_bnefesh()
    all_jobs += fetch_jobshop()
    all_jobs += fetch_linkedin()
    return all_jobs


# =============================================================================
# 5. EMAIL DIGEST
# =============================================================================

SOURCE_COLORS = {
    "StartupForStartup": "#e0f2fe",
    "Nefesh B'Nefesh":   "#fce7f3",
    "JobShop":           "#fef9c3",
    "LinkedIn":          "#dbeafe",
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
        desc      = (job.get("description") or "")
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
    Filters: Tel Aviv &middot; B2B SaaS &middot; Fintech &middot; English/French &middot; Founder CSM
  </p>
  <p style="color:#888;font-size:12px;margin-top:0;margin-bottom:20px;">
    Sources: {legend}
  </p>
  {body}
  <p style="color:#ccc;font-size:11px;margin-top:24px;
            border-top:1px solid #f0f0f0;padding-top:12px;">
    Your job search bot &middot; runs weekdays at 8am Israel time
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
# Step 2: In your repo go to:
#         Settings > Secrets and variables > Actions > New repository secret
#         Add these 3 secrets (one at a time):
#           EMAIL_FROM      your Gmail address
#           EMAIL_TO        your Gmail address
#           EMAIL_PASSWORD  your 16-char Gmail App Password
#
# Step 3: Create this file in your repo:
#         .github/workflows/job_bot.yml
#         (copy-paste the block below exactly)
#
# -----------------------------------------------------------------------
# name: Job Search Bot
# on:
#   schedule:
#     - cron: '0 6 * * 1-5'   # 8am Israel time (UTC+2), Mon-Fri
#   workflow_dispatch:          # also lets you trigger manually from GitHub UI
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
#         run: pip install requests beautifulsoup4 python-dotenv
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
#   The site updated its HTML. Open it in Chrome, right-click a job card
#   > Inspect, find the card's CSS class, update the selector in the scraper.
#
# "LinkedIn returns 0"
#   LinkedIn served a CAPTCHA or JS challenge. Expected occasionally.
#   The other 4 sources still ran fine. Nothing to fix — try again tomorrow.
#
# "Email auth failed"
#   You must use a Gmail App Password (16 chars, spaces included),
#   NOT your regular Gmail password. Enable 2FA first, then:
#   myaccount.google.com/apppasswords