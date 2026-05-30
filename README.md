# Victorian State Election 2026 — Odds Tracker

A Python tool that scrapes betting odds from multiple Australian and international bookmakers, stores daily snapshots, and generates a self-contained HTML dashboard for public viewing.

**This tool is for political analysis only — not for betting or wagering purposes.**

---

## What It Does

1. **Scrapes** decimal odds (payout on $1 AUD) from:
   - Sportsbet (direct HTML scrape)
   - Oddschecker (aggregates bet365, BetMGM, BetGoodwin, Virgin Bet, BetTom, Betfair Exchange)
   - TAB (public JSON API — requires Australian IP)
   - Betr (requires you to identify their XHR endpoint — see setup)
   - Betfair Exchange (official API — requires free account + API key)

2. **Stores** a daily snapshot in `data/odds_history.json`

3. **Generates** a static `index.html` with:
   - Current odds table across all sources (best price highlighted)
   - Weekly snapshot table (reverse chronological, closest to Sunday 5pm AEST)
   - Line graph of aggregate odds over time (Labor = red, Coalition = blue)

---

## Quick Start

### Prerequisites

```bash
pip install requests beautifulsoup4
```

### Run with demo data (to preview the dashboard)

```bash
python odds_tracker.py --seed-demo
open index.html
```

### Run a live scrape

```bash
python odds_tracker.py
```

This will scrape all configured sources, save a daily snapshot, and regenerate `index.html`.

---

## Configuration

### TAB

Works automatically from any Australian IP. No credentials needed. If you're running this from overseas, use a VPN or Australian VPS.

### Betr

Betr renders its page entirely in JavaScript, so we can't scrape the HTML directly. You need to find their internal API endpoint:

1. Open Chrome and navigate to:
   `https://www.betr.com.au/sports/Politics/142/Australian-Elections/Victorian-State-Election/198087`
2. Open DevTools (F12) → **Network** tab → filter by **Fetch/XHR**
3. Reload the page
4. Look for a request that returns JSON containing odds data (it will have prices like 1.85, 2.30, etc.)
5. Right-click that request → **Copy** → **Copy as cURL**
6. Extract the URL and set it as an environment variable:

```bash
export BETR_API_URL="https://api.betr.com.au/whatever/you/found"
```

### Betfair Exchange

1. Create a free account at [betfair.com.au](https://betfair.com.au)
2. Go to [developer.betfair.com](https://developer.betfair.com) and create an app key (free)
3. Set environment variables:

```bash
export BETFAIR_USER="your_username"
export BETFAIR_PASS="your_password"
export BETFAIR_APP_KEY="your_app_key"
```

---

## Daily Automation (cron)

Add to your crontab (`crontab -e`):

```cron
# Run at 5:05 PM AEST (07:05 UTC) every day
5 7 * * * cd /path/to/vic_election_odds && /usr/bin/python3 odds_tracker.py >> /tmp/odds_tracker.log 2>&1
```

For the Sunday weekly snapshot to be accurate, just ensure the script runs at least once on Sundays.

---

## Hosting the Dashboard

The output is a single self-contained `index.html` file (no dependencies, Chart.js loaded from CDN). You can host it anywhere:

### Option A: GitHub Pages (free, easiest)

1. Create a GitHub repo (e.g. `vic-election-odds`)
2. Push `index.html` to the `main` branch
3. Go to Settings → Pages → Source: `main` branch, root folder
4. Your dashboard is live at `https://yourusername.github.io/vic-election-odds/`
5. Set up a GitHub Action or local cron to push updated `index.html` daily:

```bash
#!/bin/bash
cd /path/to/vic_election_odds
python3 odds_tracker.py
cd /path/to/your-github-repo
cp /path/to/vic_election_odds/index.html .
git add index.html
git commit -m "Update odds $(date +%Y-%m-%d)"
git push
```

### Option B: Netlify Drop (free, drag-and-drop)

1. Go to [app.netlify.com/drop](https://app.netlify.com/drop)
2. Drag `index.html` onto the page
3. Done — you get a public URL

### Option C: Any web server

Just upload `index.html` to any web host, S3 bucket, or even a Google Drive shared folder.

---

## File Structure

```
vic_election_odds/
├── odds_tracker.py          # Main script
├── index.html               # Generated dashboard (upload this)
├── data/
│   └── odds_history.json    # Daily snapshots (keep this backed up)
└── README.md
```

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| All sources return 403 | Running from outside Australia, or site blocks `requests` | Run from an AU machine; for Sportsbet/Oddschecker, may need to add cookies or use Playwright |
| TAB returns "unavailable from your location" | Geo-blocked | Must run from Australian IP |
| Betr returns no data | JS-only rendering | Follow the DevTools instructions above to find the XHR endpoint |
| Betfair login fails | Wrong credentials or app key | Double-check env vars; ensure the account is activated |
| Chart shows no data | Only one day of data | The chart needs 2+ daily snapshots; run for a few days |

## Advanced: Using Playwright for JS-Rendered Sites

If you want to scrape Betr (or Sportsbet if they block `requests`), install Playwright:

```bash
pip install playwright
playwright install chromium
```

Then add a function like:

```python
from playwright.sync_api import sync_playwright

def scrape_with_browser(url, selector):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        content = page.content()
        browser.close()
        return content
```
