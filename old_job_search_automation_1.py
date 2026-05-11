"""
Job Search Automation — Customer Success Manager, B2B SaaS, Tel Aviv
=====================================================================
What this does every time you run it:
  1. Searches LinkedIn, Indeed, and StartupForStartup via their APIs / RSS
  2. Applies title variant matching and keyword filters
  3. Deduplicates against a local cache of already-seen listings
  4. Sends you a clean email digest with only NEW jobs

Setup (one-time):
  pip install requests feedparser python-dotenv

Create a .env file next to this script with:
  EMAIL_FROM=you@gmail.com
  EMAIL_TO=you@gmail.com
  EMAIL_PASSWORD=your_gmail_app_password   # Gmail App Password, not your main password
  LINKEDIN_EMAIL=you@linkedin.com          # Optional, for LinkedIn RSS
  RAPIDAPI_KEY=your_key                    # Optional, for Indeed via RapidAPI

Run manually or schedule via cron:
  0 8 * * 1-5 /usr/bin/python3 /path/to/job_search_automation.py
"""

import os
import json
import hashlib
import smtplib
import datetime
import requests
import feedparser
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# 1. TITLE VARIANTS
#    Every form of "Customer Success Manager" you might be hiding under.
#    The matcher is case-insensitive and looks for any of these in the job title.
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

    # Founder / first hire framing
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

    # Overlap with Account Management in B2B SaaS context
    "account manager saas",
    "strategic account manager",
    "enterprise account manager",

    # Fintech-specific variations
    "customer success fintech",
    "client relations manager fintech",
    "relationship manager saas",
]


def matches_title(job_title: str) -> bool:
    """Return True if the job title matches any of our target variants."""
    title_lower = job_title.lower()
    return any(variant in title_lower for variant in TITLE_VARIANTS)


# ─────────────────────────────────────────────────────────────────────────────
# 2. FILTER LOGIC
#    A job passes if it clears ALL of the following checks.
# ─────────────────────────────────────────────────────────────────────────────

# Location keywords — job must be in Tel Aviv or nearby / remote-from-Israel
LOCATION_INCLUDE = [
    "tel aviv", "tlv", "israel", "תל אביב", "ישראל",
    "remote", "hybrid",  # keep remote roles that allow Israel
]

# Language signals — job description should suggest English or French work env
LANGUAGE_SIGNALS = [
    "english", "french", "anglais", "français",
    # Absence of these is not a disqualifier — many IL postings don't mention lang
]

# Company type signals — we want B2B SaaS or Fintech
COMPANY_TYPE_INCLUDE = [
    "saas", "b2b", "fintech", "financial technology",
    "software", "platform", "startup", "scale-up", "series",
    # Broad enough to catch most valid companies
]

# Hard exclusions — skip roles that clearly don't fit
EXCLUDE_KEYWORDS = [
    "b2c", "consumer", "retail", "e-commerce", "gaming",
    "agency", "outsourcing", "bpo",
    "senior director",  # usually not a founder-stage role
    "internship", "intern", "student",
]


def passes_filters(job: dict) -> bool:
    """
    job dict keys expected: title, location, description, company
    Returns True if the job clears all filter gates.
    """
    title       = (job.get("title", "") or "").lower()
    location    = (job.get("location", "") or "").lower()
    description = (job.get("description", "") or "").lower()
    company     = (job.get("company", "") or "").lower()

    full_text = f"{title} {location} {description} {company}"

    # Gate 1: Title must match a variant
    if not matches_title(title):
        return False

    # Gate 2: Location must be Israel / remote
    if not any(loc in full_text for loc in LOCATION_INCLUDE):
        return False

    # Gate 3: Hard exclusions — any match disqualifies
    if any(excl in full_text for excl in EXCLUDE_KEYWORDS):
        return False

    # Gate 4 (soft): Prefer B2B SaaS / Fintech signal — warn but don't exclude
    # (We include the job and flag it if the signal is weak)
    job["saas_signal"] = any(kw in full_text for kw in COMPANY_TYPE_INCLUDE)

    return True


# ─────────────────────────────────────────────────────────────────────────────
# 3. DEDUPLICATION CACHE
#    A local JSON file that stores IDs of every listing we've seen.
#    Only jobs NOT in the cache are sent in today's digest.
# ─────────────────────────────────────────────────────────────────────────────

CACHE_FILE = os.path.join(os.path.dirname(__file__), "seen_jobs_cache.json")


def load_cache() -> set:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_cache(seen_ids: set):
    with open(CACHE_FILE, "w") as f:
        json.dump(list(seen_ids), f)


def make_job_id(job: dict) -> str:
    """Stable fingerprint: company + title (catches cross-site duplicates)."""
    raw = f"{job.get('company', '').lower().strip()}|{job.get('title', '').lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# 4. SOURCE SCRAPERS
#    Each function returns a list of job dicts:
#    { title, company, location, url, description, source }
# ─────────────────────────────────────────────────────────────────────────────

def fetch_linkedin_rss() -> list:
    """
    LinkedIn RSS feed — no auth needed for public searches.
    Note: LinkedIn RSS is unofficial and may break. 
    For reliable access use the LinkedIn Job Search API (requires partner access)
    or a service like Apify's LinkedIn Jobs Scraper.
    """
    jobs = []
    queries = [
        "customer+success+manager",
        "head+of+customer+success",
        "VP+customer+success",
    ]
    for q in queries:
        url = (
            f"https://www.linkedin.com/jobs/search/?keywords={q}"
            f"&location=Tel+Aviv&f_TPR=r86400&f_WT=1,2,3"  # past 24h
            f"&trk=public_jobs_jobs-search-bar_search-submit"
        )
        # LinkedIn doesn't offer a clean RSS — this URL opens the web UI.
        # For automation, use Apify (see APIFY ALTERNATIVE below) or
        # linkedin_jobs_scraper Python package.
        jobs.append({
            "title": "⚠ LinkedIn requires Apify or API — see comments",
            "company": "",
            "location": "Tel Aviv",
            "url": url,
            "description": "",
            "source": "LinkedIn (manual fallback)",
        })
        break  # placeholder — remove when real scraper is wired

    return jobs


def fetch_indeed_rss() -> list:
    """
    Indeed Israel RSS feed — publicly available, no auth needed.
    """
    jobs = []
    queries = ["customer+success+manager", "customer+success+lead", "head+of+customer+success"]

    for q in queries:
        rss_url = (
            f"https://il.indeed.com/rss?q={q}&l=Tel+Aviv&fromage=1&sort=date"
        )
        try:
            feed = feedparser.parse(rss_url)
            for entry in feed.entries:
                jobs.append({
                    "title":       entry.get("title", ""),
                    "company":     entry.get("author", ""),
                    "location":    "Tel Aviv",
                    "url":         entry.get("link", ""),
                    "description": entry.get("summary", ""),
                    "source":      "Indeed IL",
                })
        except Exception as e:
            print(f"Indeed RSS error: {e}")

    return jobs


def fetch_startup_for_startup() -> list:
    """
    StartupForStartup (startupforstartup.com) — Israeli startup job board.
    No public API — uses their web search. 
    For production use, run this via Apify's generic web scraper
    pointed at: https://startupforstartup.com/jobs/?q=customer+success
    """
    jobs = []
    # Placeholder: In production, parse HTML from their jobs page
    # using BeautifulSoup or Apify. Structure example:
    example = {
        "title":       "Customer Success Manager",
        "company":     "Example Startup",
        "location":    "Tel Aviv",
        "url":         "https://startupforstartup.com/jobs/example",
        "description": "B2B SaaS, English-speaking environment",
        "source":      "StartupForStartup",
    }
    # jobs.append(example)  # uncomment when scraper is wired
    print("StartupForStartup: wire Apify scraper — see APIFY ALTERNATIVE section")
    return jobs


def fetch_lastartup() -> list:
    """
    LaStartup (lastartup.co.il) — Israeli tech job board.
    No public API — scrape their jobs page.
    """
    jobs = []
    print("LaStartup: wire Apify scraper pointed at lastartup.co.il/jobs")
    return jobs


def fetch_all_sources() -> list:
    """Aggregate jobs from all sources."""
    all_jobs = []
    all_jobs += fetch_indeed_rss()
    all_jobs += fetch_linkedin_rss()
    all_jobs += fetch_startup_for_startup()
    all_jobs += fetch_lastartup()
    return all_jobs


# ─────────────────────────────────────────────────────────────────────────────
# 5. APIFY ALTERNATIVE (recommended for sites without RSS)
#    Apify is a no-code scraping platform with a free tier (100 actor runs/mo).
#    Set APIFY_TOKEN in your .env and uncomment the actors you want.
# ─────────────────────────────────────────────────────────────────────────────

def fetch_via_apify(actor_id: str, run_input: dict) -> list:
    """
    Generic Apify actor runner.
    actor_id examples:
      'apify/linkedin-jobs-scraper'
      'apify/indeed-scraper'
      'apify/website-content-crawler' (for StartupForStartup, LaStartup)
    """
    token = os.getenv("APIFY_TOKEN")
    if not token:
        print(f"Apify: no token set, skipping {actor_id}")
        return []

    try:
        # Start actor run
        run_resp = requests.post(
            f"https://api.apify.com/v2/acts/{actor_id}/runs",
            headers={"Authorization": f"Bearer {token}"},
            json={"runInput": run_input, "waitForFinish": 120},
        )
        run_data = run_resp.json()
        dataset_id = run_data.get("data", {}).get("defaultDatasetId")
        if not dataset_id:
            return []

        # Fetch results
        items_resp = requests.get(
            f"https://api.apify.com/v2/datasets/{dataset_id}/items",
            headers={"Authorization": f"Bearer {token}"},
            params={"format": "json"},
        )
        return items_resp.json() or []

    except Exception as e:
        print(f"Apify error ({actor_id}): {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 6. EMAIL DIGEST
#    Sends a clean HTML email with today's new listings.
# ─────────────────────────────────────────────────────────────────────────────

def build_email_html(new_jobs: list) -> str:
    today = datetime.date.today().strftime("%A, %d %B %Y")
    count = len(new_jobs)

    rows = ""
    for job in new_jobs:
        saas_badge = (
            '<span style="background:#d1fae5;color:#065f46;padding:2px 8px;'
            'border-radius:4px;font-size:11px;margin-left:8px;">✓ SaaS signal</span>'
            if job.get("saas_signal") else
            '<span style="background:#fef3c7;color:#92400e;padding:2px 8px;'
            'border-radius:4px;font-size:11px;margin-left:8px;">? Check company</span>'
        )
        rows += f"""
        <tr>
          <td style="padding:14px 16px;border-bottom:1px solid #f0f0f0;">
            <div style="font-weight:600;font-size:15px;">
              <a href="{job['url']}" style="color:#1a1a1a;text-decoration:none;">
                {job['title']}
              </a>
              {saas_badge}
            </div>
            <div style="color:#555;font-size:13px;margin-top:4px;">
              {job.get('company', 'Unknown company')} &nbsp;·&nbsp; {job.get('location', '')}
              &nbsp;·&nbsp; <span style="color:#888;">{job.get('source', '')}</span>
            </div>
            <div style="color:#777;font-size:12px;margin-top:6px;line-height:1.5;">
              {job.get('description', '')[:200]}{'…' if len(job.get('description','')) > 200 else ''}
            </div>
          </td>
        </tr>
        """

    if count == 0:
        body = "<p style='color:#555;'>No new listings today. Check back tomorrow.</p>"
    else:
        body = f"""
        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;
               border:1px solid #e8e8e8;border-radius:8px;overflow:hidden;">
          {rows}
        </table>
        """

    return f"""
    <html><body style="font-family:sans-serif;max-width:680px;margin:0 auto;padding:24px;">
      <h2 style="font-size:20px;margin-bottom:4px;">
        🎯 {count} new CS job{'s' if count != 1 else ''} · {today}
      </h2>
      <p style="color:#888;font-size:13px;margin-top:0;margin-bottom:20px;">
        Filters: Tel Aviv · B2B SaaS · Fintech · English/French · Founder CSM roles
      </p>
      {body}
      <p style="color:#bbb;font-size:11px;margin-top:24px;">
        Sent by your job search bot · <a href="mailto:{os.getenv('EMAIL_TO')}">unsubscribe</a>
      </p>
    </body></html>
    """


def send_email(new_jobs: list):
    sender   = os.getenv("EMAIL_FROM")
    receiver = os.getenv("EMAIL_TO")
    password = os.getenv("EMAIL_PASSWORD")

    if not all([sender, receiver, password]):
        print("Email not configured — set EMAIL_FROM, EMAIL_TO, EMAIL_PASSWORD in .env")
        return

    count = len(new_jobs)
    subject = (
        f"🎯 {count} new CSM job{'s' if count != 1 else ''} in Tel Aviv today"
        if count > 0 else
        "Job search bot — no new listings today"
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
        print(f"✓ Email sent: {count} new jobs")
    except Exception as e:
        print(f"Email error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 7. MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"Job search bot running — {datetime.datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*60}\n")

    # Load what we've already seen
    seen_ids = load_cache()
    print(f"Cache: {len(seen_ids)} listings already seen\n")

    # Pull from all sources
    raw_jobs = fetch_all_sources()
    print(f"Fetched: {len(raw_jobs)} raw listings\n")

    # Apply filters
    filtered = [j for j in raw_jobs if passes_filters(j)]
    print(f"After filters: {len(filtered)} listings\n")

    # Deduplicate — only keep jobs not in cache
    new_jobs = []
    new_ids  = set()
    for job in filtered:
        job_id = make_job_id(job)
        if job_id not in seen_ids:
            new_jobs.append(job)
            new_ids.add(job_id)

    print(f"New today: {len(new_jobs)} listings\n")

    # Update cache
    seen_ids.update(new_ids)
    save_cache(seen_ids)

    # Send digest
    send_email(new_jobs)

    # Print summary to console
    for job in new_jobs:
        print(f"  [{job['source']}] {job['title']} — {job.get('company','')}")
    print()


if __name__ == "__main__":
    main()


# ─────────────────────────────────────────────────────────────────────────────
# NEXT STEPS
# ─────────────────────────────────────────────────────────────────────────────
#
# Phase 1 — works today (no paid API):
#   ✓ Indeed IL RSS        (fetch_indeed_rss)
#   ✓ Deduplication cache
#   ✓ Email digest
#
# Phase 2 — add these for full coverage:
#   □ LinkedIn via Apify actor 'apify/linkedin-jobs-scraper'
#   □ StartupForStartup via Apify website crawler
#   □ LaStartup via Apify website crawler
#   □ Nefesh B'Nefesh via Apify (https://nbnjobs.com)
#
# Phase 3 — schedule it:
#   Mac/Linux cron:  0 8 * * 1-5 python3 /path/to/job_search_automation.py
#   Windows Task Scheduler: point to pythonw.exe with the script path
#   Free cloud option: GitHub Actions (free, runs on schedule, no server needed)
#
# GitHub Actions schedule example (.github/workflows/job_bot.yml):
#   on:
#     schedule:
#       - cron: '0 6 * * 1-5'   # 8am Israel time (UTC+2)
#   jobs:
#     run:
#       runs-on: ubuntu-latest
#       steps:
#         - uses: actions/checkout@v3
#         - run: pip install requests feedparser python-dotenv
#         - run: python job_search_automation.py
#       env:
#         EMAIL_FROM: ${{ secrets.EMAIL_FROM }}
#         EMAIL_TO: ${{ secrets.EMAIL_TO }}
#         EMAIL_PASSWORD: ${{ secrets.EMAIL_PASSWORD }}
#         APIFY_TOKEN: ${{ secrets.APIFY_TOKEN }}
