
Job Search Automation — Customer Success Manager, B2B SaaS, Tel Aviv
=====================================================================
100% free. No RSS. No Apify. No paid APIs.

Sources scraped (BeautifulSoup + requests):
  - StartupForStartup  (startupforstartup.com)
  - LaStartup          (lastartup.co.il)
  - Nefesh B'Nefesh    (nbnjobs.com)
  - JobShop            (jobshop.co.il)
  - LinkedIn           (best-effort, graceful fail if blocked)

HOW SCRAPING WORKS ON YOUR PC vs. A SERVER
  These job sites block requests coming from cloud/datacenter IPs (standard
  Cloudflare protection). From your home Windows PC, your residential IP is
  not flagged and all sites return 200 OK. The script is designed to run
  locally or via GitHub Actions, which uses a residential-adjacent IP pool.

ONE-TIME SETUP
  pip install requests beautifulsoup4 python-dotenv

Create a .env file in the same folder as this script:
  EMAIL_FROM=you@gmail.com
  EMAIL_TO=you@gmail.com
  EMAIL_PASSWORD=xxxx xxxx xxxx xxxx   <- 16-char Gmail App Password
                                          (not your regular Gmail password)
  Get one at: myaccount.google.com/apppasswords

PASSWORD SAFETY
  - .env never leaves your machine (blocked by .gitignore)
  - GitHub only ever sees the Python script, never your passwords
  - Passwords on GitHub are stored in encrypted Secrets vault

RUN MANUALLY
  python job_search_automation.py

SCHEDULE FREE (weekdays 8am Israel time, no computer left on)
  See SCHEDULING section at the bottom — uses GitHub Actions.
