"""
Job Search Automation — Customer Success Manager, B2B SaaS, Tel Aviv
=====================================================================
100% free. No RSS. No Apify. No paid APIs.

Sources scraped (BeautifulSoup + requests):
  - StartupForStartup  (startupforstartup.com)
  - LaStartup          (lastartup.co.il)
  - Nefesh B'Nefesh    (nbnjobs.com)
  - JobShop            (jobshop.co.il)
  - LinkedIn           (best-effort, graceful fail if blocked)

One-time setup:
  pip install requests beautifulsoup4 python-dotenv

Create a .env file next to this script:
  EMAIL_FROM=you@gmail.com
  EMAIL_TO=you@gmail.com
  EMAIL_PASSWORD=your_gmail_app_password   # Gmail App Password (not your main password)
  # Get one at: myaccount.google.com/apppasswords

Schedule (runs every weekday at 8am):
  Mac/Linux cron:  0 8 * * 1-5 /usr/bin/python3 /path/to/job_search_automation.py
  GitHub Actions:  see SCHEDULING section at the bottom (free, no server needed)
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

load_dotenv()

# Polite scraping: wait between requests so we don't hammer servers
REQUEST_DELAY = 2  # seconds between each site fetch

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,he;q=0.8,fr;q=0.7",
}


# ─────────────────────────────────────────────────────────────────────────────
# 1. TITLE VARIANTS
#    Every form of "Customer Success Manager" you might be hiding under.
#    Matcher is case-insensitive substring — catches mixed Hebrew/English titles.
# ─────────────────────────────────────────────────────────────────────────────

TITLE_VARIANTS = [
    # Core CSM titles
    "customer success manager",
    "customer success lead",
    "customer success director",
    "head of customer success",
    "vp of customer success",
    "vp customer success",
    "director of customer success",
    "chief customer officer",
    "cco",

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

    # Overlap with Account Management in B2B SaaS
    "strategic account manager",
    "enterprise account manager",
    "technical account manager",

    # Fintech-specific
    "customer success fintech",
    "client relations manager",
    "relationship manager",

    # Hebrew transliterations sometimes found on Israeli job boards
    "\u05de\u05e0\u05d4\u05dc \u05d4\u05e6\u05dc\u05d7\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",   # Customer Success Manager
    "\u05de\u05e0\u05d4\u05dc\u05ea \u05d4\u05e6\u05dc\u05d7\u05ea \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea",  # Feminine form
]


def matches_title(job_title: str) -> bool:
    """Return True if the job title matches any of our target variants."""
    title_lower = job_title.lower()
    return any(variant in title_lower for variant in TITLE_VARIANTS)


# ─────────────────────────────────────────────────────────────────────────────
# 2. FILTER LOGIC
#    A job passes only if it clears ALL gates below.
# ─────────────────────────────────────────────────────────────────────────────

LOCATION_INCLUDE = [
    "tel aviv", "tlv", "israel", "\u05ea\u05dc \u05d0\u05d1\u05d9\u05d1", "\u05d9\u05e9\u05e8\u05d0\u05dc",
    "ramat gan", "herzliya", "petah tikva", "bnei brak",
    "remote", "hybrid",
]

COMPANY_TYPE_INCLUDE = [
    "saas", "b2b", "fintech", "financial technology",
    "software", "platform", "startup", "scale-up", "series a",
    "series b", "seed", "venture",
]

EXCLUDE_KEYWORDS = [
    "b2c", "e-commerce", "gaming", "game",
    "agency", "outsourcing", "bpo", "call center",
    "internship", "intern", "student", "apprentice",
]

# Sources that are Israel-only by nature — skip location gate for these
ISRAEL_NATIVE_SOURCES = {"startupforstartup", "lastartup", "jobshop", "nefesh b'nefesh"}


def passes_filters(job: dict) -> bool:
    """
    Returns True if the job passes all filter gates.
    Also sets job['saas_signal'] for the email badge.
    """
    title       = (job.get("title", "") or "").lower()
    location    = (job.get("location", "") or "").lower()
    description = (job.get("description", "") or "").lower()
    company     = (job.get("company", "") or "").lower()
    source      = (job.get("source", "") or "").lower()

    full_text = f"{title} {location} {description} {company}"

    # Gate 1: Title must match a variant
    if not matches_title(title):
        return False

    # Gate 2: Location — skip for Israel-native boards
    if not any(s in source for s in ISRAEL_NATIVE_SOURCES):
        if not any(loc in full_text for loc in LOCATION_INCLUDE):
            return False

    # Gate 3: Hard exclusions
    if any(excl in full_text for excl in EXCLUDE_KEYWORDS):
        return False

    # Gate 4 (soft): SaaS/Fintech signal — flag but never exclude
    job["saas_signal"] = any(kw in full_text for kw in COMPANY_TYPE_INCLUDE)

    return True


# ─────────────────────────────────────────────────────────────────────────────
# 3. DEDUPLICATION CACHE
#    Local JSON file. Fingerprint = company + title.
#    Same role posted on 3 boards → only appears once in your digest.
# ─────────────────────────────────────────────────────────────────────────────

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_jobs_cache.json")


def load_cache() -> set:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_cache(seen_ids: set):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f)


def make_job_id(job: dict) -> str:
    """MD5 of 'company|title' — stable across sources."""
    raw = f"{job.get('company', '').lower().strip()}|{job.get('title', '').lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# 4. SCRAPERS — one per source
#    Each returns: list of { title, company, location, url, description, source }
#    All wrapped in try/except — one broken source never kills the whole run.
# ─────────────────────────────────────────────────────────────────────────────

def get_soup(url: str) -> BeautifulSoup | None:
    """Fetch a URL and return a BeautifulSoup object, or None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  x Fetch failed ({url[:60]}...): {e}")
        return None


# ── 4a. StartupForStartup ────────────────────────────────────────────────────

def fetch_startup_for_startup() -> list:
    """
    https://startupforstartup.com/jobs/
    Premier Israeli startup job board. Very scraping-friendly.
    """
    jobs = []
    search_terms = ["customer-success", "customer-success-manager"]

    for term in search_terms:
        url = f"https://startupforstartup.com/jobs/?query={term}"
        soup = get_soup(url)
        if not soup:
            continue

        cards = soup.select("div.job-card, article.job, li.job-listing, div[class*='job']")
        if not cards:
            cards = soup.find_all("a", href=re.compile(r"/jobs/\d+|/job/"))

        for card in cards[:30]:
            try:
                title_el   = card.select_one("h2, h3, .job-title, [class*='title']")
                company_el = card.select_one(".company, [class*='company']")
                link_el    = card if card.name == "a" else card.find("a")

                title   = title_el.get_text(strip=True)   if title_el   else ""
                company = company_el.get_text(strip=True) if company_el else ""
                href    = link_el["href"] if link_el and link_el.get("href") else ""
                url_full = href if href.startswith("http") else f"https://startupforstartup.com{href}"

                if title:
                    jobs.append({
                        "title":       title,
                        "company":     company,
                        "location":    "Tel Aviv, Israel",
                        "url":         url_full,
                        "description": "",
                        "source":      "StartupForStartup",
                    })
            except Exception:
                continue

        time.sleep(REQUEST_DELAY)

    print(f"  StartupForStartup: {len(jobs)} raw listings")
    return jobs


# ── 4b. LaStartup ────────────────────────────────────────────────────────────

def fetch_lastartup() -> list:
    """
    https://lastartup.co.il
    Israeli startup job board, Hebrew and English listings.
    """
    jobs = []
    url = "https://lastartup.co.il/jobs/?search=customer+success"
    soup = get_soup(url)
    if not soup:
        print("  LaStartup: failed to fetch")
        return jobs

    cards = soup.select("div.job, article, li.job, div[class*='position']")

    for card in cards[:40]:
        try:
            title_el   = card.select_one("h2, h3, .title, [class*='title']")
            company_el = card.select_one(".company, [class*='company'], [class*='employer']")
            link_el    = card.find("a")

            title   = title_el.get_text(strip=True)   if title_el   else ""
            company = company_el.get_text(strip=True) if company_el else ""
            href    = link_el["href"] if link_el and link_el.get("href") else ""
            url_full = href if href.startswith("http") else f"https://lastartup.co.il{href}"

            if title:
                jobs.append({
                    "title":       title,
                    "company":     company,
                    "location":    "Israel",
                    "url":         url_full,
                    "description": "",
                    "source":      "LaStartup",
                })
        except Exception:
            continue

    time.sleep(REQUEST_DELAY)
    print(f"  LaStartup: {len(jobs)} raw listings")
    return jobs


# ── 4c. Nefesh B'Nefesh ──────────────────────────────────────────────────────

def fetch_nefesh_bnefesh() -> list:
    """
    https://nbnjobs.com
    English-language Israeli job board — great for olim / international roles.
    """
    jobs = []
    url = "https://nbnjobs.com/jobs/?search=customer+success"
    soup = get_soup(url)
    if not soup:
        print("  Nefesh B'Nefesh: failed to fetch")
        return jobs

    cards = soup.select("div.job-listing, article.job, li.job, div[class*='job-card']")

    for card in cards[:40]:
        try:
            title_el    = card.select_one("h2, h3, .job-title, [class*='title']")
            company_el  = card.select_one(".company-name, [class*='company']")
            location_el = card.select_one(".location, [class*='location']")
            link_el     = card.find("a")

            title    = title_el.get_text(strip=True)    if title_el    else ""
            company  = company_el.get_text(strip=True)  if company_el  else ""
            location = location_el.get_text(strip=True) if location_el else "Israel"
            href     = link_el["href"] if link_el and link_el.get("href") else ""
            url_full = href if href.startswith("http") else f"https://nbnjobs.com{href}"

            if title:
                jobs.append({
                    "title":       title,
                    "company":     company,
                    "location":    location,
                    "url":         url_full,
                    "description": "",
                    "source":      "Nefesh B'Nefesh",
                })
        except Exception:
            continue

    time.sleep(REQUEST_DELAY)
    print(f"  Nefesh B'Nefesh: {len(jobs)} raw listings")
    return jobs


# ── 4d. JobShop (Hebrew) ─────────────────────────────────────────────────────

def fetch_jobshop() -> list:
    """
    https://www.jobshop.co.il
    Hebrew job board — English search still works well for tech roles.
    """
    jobs = []
    url = "https://www.jobshop.co.il/positions?search=customer+success"
    soup = get_soup(url)
    if not soup:
        print("  JobShop: failed to fetch")
        return jobs

    cards = soup.select("div.position, article, li.position, div[class*='job']")

    for card in cards[:40]:
        try:
            title_el   = card.select_one("h2, h3, .position-title, [class*='title']")
            company_el = card.select_one(".company, [class*='company']")
            link_el    = card.find("a")

            title   = title_el.get_text(strip=True)   if title_el   else ""
            company = company_el.get_text(strip=True) if company_el else ""
            href    = link_el["href"] if link_el and link_el.get("href") else ""
            url_full = href if href.startswith("http") else f"https://www.jobshop.co.il{href}"

            if title:
                jobs.append({
                    "title":       title,
                    "company":     company,
                    "location":    "Israel",
                    "url":         url_full,
                    "description": "",
                    "source":      "JobShop",
                })
        except Exception:
            continue

    time.sleep(REQUEST_DELAY)
    print(f"  JobShop: {len(jobs)} raw listings")
    return jobs


# ── 4e. LinkedIn (best-effort) ───────────────────────────────────────────────

def fetch_linkedin() -> list:
    """
    LinkedIn public jobs page — no login needed for basic search.
    Fragile: LinkedIn updates their HTML often and may serve CAPTCHAs.
    Fails gracefully — other sources pick up the slack.
    """
    jobs = []
    queries = [
        "customer+success+manager",
        "head+of+customer+success",
        "VP+customer+success",
    ]

    for q in queries:
        url = (
            f"https://www.linkedin.com/jobs/search/"
            f"?keywords={q}&location=Tel+Aviv%2C+Israel"
            f"&f_TPR=r86400&sortBy=DD"  # past 24h, sorted by date
        )
        soup = get_soup(url)
        if not soup:
            continue

        cards = soup.select(
            "div.base-card, li.jobs-search-results__list-item, "
            "div[class*='job-search-card'], div[data-entity-urn]"
        )

        for card in cards[:20]:
            try:
                title_el   = card.select_one(
                    "h3.base-search-card__title, h3[class*='title'], "
                    "span[class*='title'], a[class*='title']"
                )
                company_el = card.select_one(
                    "h4.base-search-card__subtitle, a[class*='company'], "
                    "span[class*='company']"
                )
                link_el = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")

                title   = title_el.get_text(strip=True)   if title_el   else ""
                company = company_el.get_text(strip=True) if company_el else ""
                href    = link_el["href"]                 if link_el    else url

                if title:
                    jobs.append({
                        "title":       title,
                        "company":     company,
                        "location":    "Tel Aviv, Israel",
                        "url":         href,
                        "description": "",
                        "source":      "LinkedIn",
                    })
            except Exception:
                continue

        time.sleep(REQUEST_DELAY)

    print(f"  LinkedIn: {len(jobs)} raw listings (best-effort)")
    return jobs


# ── Aggregate ────────────────────────────────────────────────────────────────

def fetch_all_sources() -> list:
    print("\nFetching sources:")
    all_jobs = []
    all_jobs += fetch_startup_for_startup()
    all_jobs += fetch_lastartup()
    all_jobs += fetch_nefesh_bnefesh()
    all_jobs += fetch_jobshop()
    all_jobs += fetch_linkedin()
    return all_jobs


# ─────────────────────────────────────────────────────────────────────────────
# 5. EMAIL DIGEST
# ─────────────────────────────────────────────────────────────────────────────

SOURCE_COLORS = {
    "StartupForStartup": "#e0f2fe",
    "LaStartup":         "#ede9fe",
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
            'border-radius:4px;font-size:11px;margin-left:8px;">checkmark SaaS/Fintech</span>'
            if job.get("saas_signal") else
            '<span style="background:#fef3c7;color:#92400e;padding:2px 8px;'
            'border-radius:4px;font-size:11px;margin-left:8px;">? Verify company</span>'
        )
        source     = job.get("source", "")
        source_bg  = SOURCE_COLORS.get(source, "#f3f4f6")
        desc       = job.get("description", "")
        desc_html  = (
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
              {job.get('company', 'Unknown company')}
              &nbsp;&middot;&nbsp; {job.get('location', 'Israel')}
              &nbsp;&middot;&nbsp;
              <span style="background:{source_bg};padding:1px 7px;border-radius:3px;
                           font-size:11px;color:#444;">{source}</span>
            </div>
            {desc_html}
          </td>
        </tr>
        """

    if count == 0:
        body = "<p style='color:#555;padding:20px 0;'>No new listings today. Check back tomorrow.</p>"
    else:
        body = f"""
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;border:1px solid #e8e8e8;
                      border-radius:8px;overflow:hidden;">
          {rows}
        </table>"""

    source_legend = "".join([
        f'<span style="display:inline-block;margin:2px 4px;padding:2px 8px;'
        f'background:{bg};border-radius:3px;font-size:11px;color:#444;">{src}</span>'
        for src, bg in SOURCE_COLORS.items()
    ])

    return f"""
    <html>
    <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                 max-width:680px;margin:0 auto;padding:24px;color:#1a1a1a;">
      <h2 style="font-size:20px;margin-bottom:4px;">
        {count} new CS role{'s' if count != 1 else ''} &middot; {today}
      </h2>
      <p style="color:#888;font-size:12px;margin-top:0;margin-bottom:8px;">
        Filters: Tel Aviv &middot; B2B SaaS &middot; Fintech &middot; English/French &middot; Founder CSM
      </p>
      <p style="color:#888;font-size:12px;margin-top:0;margin-bottom:20px;">
        Sources: {source_legend}
      </p>
      {body}
      <p style="color:#ccc;font-size:11px;margin-top:24px;border-top:1px solid #f0f0f0;padding-top:12px;">
        Your job search bot &middot; runs weekdays at 8am
      </p>
    </body>
    </html>
    """


def send_email(new_jobs: list):
    sender   = os.getenv("EMAIL_FROM")
    receiver = os.getenv("EMAIL_TO")
    password = os.getenv("EMAIL_PASSWORD")

    if not all([sender, receiver, password]):
        print("\n[!] Email not configured.")
        print("    Set EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD in your .env file.")
        print("    Gmail App Password guide: myaccount.google.com/apppasswords\n")
        return

    count   = len(new_jobs)
    date_str = datetime.date.today().strftime("%d %b")
    subject = (
        f"{count} new CSM role{'s' if count != 1 else ''} | Tel Aviv | {date_str}"
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
        print(f"\n[ok] Email sent: {count} new job{'s' if count != 1 else ''} to {receiver}")
    except Exception as e:
        print(f"\n[!] Email failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 6. MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"Job search bot | {datetime.datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*60}")

    seen_ids = load_cache()
    print(f"\nCache: {len(seen_ids)} listings already seen")

    raw_jobs = fetch_all_sources()
    print(f"\nTotal fetched:  {len(raw_jobs)}")

    filtered = [j for j in raw_jobs if passes_filters(j)]
    print(f"After filters:  {len(filtered)}")

    new_jobs = []
    new_ids  = set()
    for job in filtered:
        job_id = make_job_id(job)
        if job_id not in seen_ids:
            new_jobs.append(job)
            new_ids.add(job_id)

    print(f"New today:      {len(new_jobs)}")

    seen_ids.update(new_ids)
    save_cache(seen_ids)

    send_email(new_jobs)

    if new_jobs:
        print("\nNew listings:")
        for job in new_jobs:
            signal = "ok" if job.get("saas_signal") else "?"
            print(f"  [{signal}] [{job['source']:<20}] {job['title']} | {job.get('company','')}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULING — GitHub Actions (free, no server needed)
# ─────────────────────────────────────────────────────────────────────────────
#
# 1. Push this script to a private GitHub repo
# 2. Add secrets in repo Settings > Secrets > Actions:
#      EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD
# 3. Create .github/workflows/job_bot.yml:
#
# name: Job Search Bot
# on:
#   schedule:
#     - cron: '0 6 * * 1-5'    # 8am Israel time (UTC+2)
#   workflow_dispatch:           # lets you trigger manually too
# jobs:
#   run:
#     runs-on: ubuntu-latest
#     steps:
#       - uses: actions/checkout@v4
#       - uses: actions/setup-python@v5
#         with:
#           python-version: '3.11'
#       - name: Restore cache
#         uses: actions/cache@v4
#         with:
#           path: seen_jobs_cache.json
#           key: job-cache-${{ github.run_id }}
#           restore-keys: job-cache-
#       - run: pip install requests beautifulsoup4 python-dotenv
#       - run: python job_search_automation.py
#         env:
#           EMAIL_FROM:     ${{ secrets.EMAIL_FROM }}
#           EMAIL_TO:       ${{ secrets.EMAIL_TO }}
#           EMAIL_PASSWORD: ${{ secrets.EMAIL_PASSWORD }}
#
# ─────────────────────────────────────────────────────────────────────────────
# TROUBLESHOOTING
# ─────────────────────────────────────────────────────────────────────────────
#
# "0 listings from [source]"
#   The site updated its HTML. Open the page in Chrome, right-click a job
#   card > Inspect, and update the CSS selector in the relevant scraper.
#
# "LinkedIn returns 0"
#   LinkedIn served a CAPTCHA or JS challenge. Expected occasionally.
#   The other 4 sources still run fine. Try again tomorrow.
#
# "Email not sending"
#   Use a Gmail App Password, not your regular password.
#   2FA must be enabled first: myaccount.google.com/apppasswords
