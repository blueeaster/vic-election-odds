#!/usr/bin/env python3
"""
Victorian State Election 2026 - Odds Tracker
Scrapes betting odds, stores daily snapshots, generates static HTML dashboard.

Usage:
  python3 odds_tracker.py                # scrape + rebuild HTML
  python3 odds_tracker.py --build-only   # rebuild HTML from stored data
  python3 odds_tracker.py --seed-demo    # inject demo data for testing

Dependencies:
  pip3 install requests beautifulsoup4 playwright --user
  python3 -m playwright install chromium
"""

import argparse
import datetime
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).parent / "data"
DATA_FILE = DATA_DIR / "odds_history.json"
OUTPUT_HTML = Path(__file__).parent / "index.html"
OUTCOMES = ["Labor", "Coalition", "Other"]


# ---------------------------------------------------------------------------
# SCRAPERS
# ---------------------------------------------------------------------------

def scrape_sportsbet():
    url = (
        "https://www.sportsbet.com.au/betting/politics/vic-politics/"
        "victorian-state-election-sworn-in-government-9186918"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        text = r.text
        odds = {}
        search_map = [
            ("Labor", '"name":"Labor"'),
            ("Coalition", '"name":"Coalition"'),
            ("Other", '"name":"Any Other Result"'),
        ]
        for outcome, needle in search_map:
            idx = text.find(needle)
            while idx != -1:
                chunk = text[idx:idx + 300]
                m = re.search(
                    r'"winPrice"\s*:\s*\{"num"\s*:\s*(\d+)\s*,\s*"den"\s*:\s*(\d+)\}',
                    chunk,
                )
                if m:
                    num = int(m.group(1))
                    den = int(m.group(2))
                    if den > 0:
                        odds[outcome] = round(num / den + 1, 2)
                    break
                idx = text.find(needle, idx + 1)
        if odds:
            return {"source": "Sportsbet", "odds": odds}
    except Exception as e:
        print("  [!] Sportsbet scrape failed: {}".format(e), file=sys.stderr)
    return None


def scrape_tab():
    api_url = (
        "https://api.beta.tab.com.au/v1/tab-info-service/sports/Politics/"
        "competitions/Victorian%20Politics/markets?jurisdiction=VIC"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36"
        ),
        "Accept": "application/json",
    }
    try:
        r = requests.get(api_url, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            odds = {}
            matches = data.get("matches", [])
            for match in matches:
                for market in match.get("markets", []):
                    for prop in market.get("propositions", []):
                        name = prop.get("name", "")
                        win = prop.get("returnWin", 0)
                        if "labor" in name.lower() or "labour" in name.lower():
                            odds["Labor"] = win
                        elif "coalition" in name.lower() or "liberal" in name.lower():
                            odds["Coalition"] = win
                        elif "other" in name.lower():
                            odds["Other"] = win
            if odds:
                return {"source": "TAB", "odds": odds}
        else:
            print("  [!] TAB returned {} (geo-blocked or wrong endpoint?)".format(r.status_code), file=sys.stderr)
    except Exception as e:
        print("  [!] TAB scrape failed: {}".format(e), file=sys.stderr)
    return None


def scrape_oddschecker():
    BOOKIE_MAP = {
        "B3": "bet365", "WH": "William Hill", "UN": "Unibet",
        "FR": "Betfred", "SX": "Spreadex", "LD": "Ladbrokes UK",
        "VC": "BetVictor", "KN": "BetMGM", "BY": "BoyleSports",
        "OE": "10bet", "S6": "Star Sports", "PUP": "PricedUp",
        "G5": "BetGoodwin", "VE": "Virgin Bet", "QN": "QuinnBet",
        "WA": "Betway", "CE": "Coral", "BTT": "BetTom",
        "BRS": "BresBet", "SK": "SkyBet", "PP": "Paddy Power",
        "AKB": "AK Bets", "BF": "Betfair", "MA": "Matchbook",
    }
    OUTCOME_MAP = {
        "australian labor party": "Labor",
        "labor": "Labor",
        "labour": "Labor",
        "coalition": "Coalition",
        "any other party": "Other",
        "any other": "Other",
        "other": "Other",
    }

    url = (
        "https://www.oddschecker.com/politics/australian-politics/"
        "state-elections/victoria-state-election"
    )

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/148.0.0.0 Safari/537.36"
                ),
                locale="en-GB",
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            try:
                page.wait_for_selector("tr[data-bname]", timeout=10000)
            except Exception:
                pass
            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, "html.parser")
        rows = soup.find_all("tr", attrs={"data-bname": True})
        if not rows:
            rows = soup.find_all("tr", attrs={"data-name": True})

        if not rows:
            print("  [!] Oddschecker: no runner rows found after browser load", file=sys.stderr)
            return []

        bookie_odds = defaultdict(dict)

        for row in rows:
            runner_name = (row.get("data-bname") or row.get("data-name") or "").lower().strip()
            outcome = None
            for key, val in OUTCOME_MAP.items():
                if key in runner_name:
                    outcome = val
                    break
            if not outcome:
                continue

            cells = row.find_all("td", attrs={"data-bk": True})
            for cell in cells:
                bk_code = cell.get("data-bk", "")
                if bk_code not in BOOKIE_MAP:
                    continue
                decimal_odds = None
                odig = cell.get("data-odig")
                if odig:
                    try:
                        decimal_odds = round(float(odig), 2)
                    except ValueError:
                        pass
                if not decimal_odds:
                    frac = cell.get("data-o")
                    if frac and "/" in frac:
                        try:
                            num, den = frac.split("/")
                            decimal_odds = round(int(num) / int(den) + 1, 2)
                        except Exception:
                            pass
                if decimal_odds and decimal_odds > 1.0:
                    bookie_odds[BOOKIE_MAP[bk_code]][outcome] = decimal_odds

        sources = [{"source": s, "odds": o} for s, o in bookie_odds.items() if o]
        print("    + {} bookmakers from Oddschecker".format(len(sources)))
        return sources

    except Exception as e:
        print("  [!] Oddschecker scrape failed: {}".format(e), file=sys.stderr)
        return []


def scrape_betr():
    betr_url = os.environ.get("BETR_API_URL", "")
    if not betr_url:
        print("  [!] Betr: BETR_API_URL not configured - skipping", file=sys.stderr)
        return None
    try:
        r = requests.get(betr_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=10)
        if r.status_code == 200:
            print("  [i] Betr returned data - parse logic needed", file=sys.stderr)
            print("  [i] First 300 chars: {}".format(r.text[:300]), file=sys.stderr)
    except Exception as e:
        print("  [!] Betr scrape failed: {}".format(e), file=sys.stderr)
    return None


def scrape_betfair():
    app_key = os.environ.get("BETFAIR_APP_KEY", "")
    username = os.environ.get("BETFAIR_USER", "")
    password = os.environ.get("BETFAIR_PASS", "")
    if not all([app_key, username, password]):
        print("  [!] Betfair: credentials not configured - skipping", file=sys.stderr)
        return None
    market_id = "1.240828208"
    try:
        login_resp = requests.post(
            "https://identitysso.betfair.com.au/api/login",
            data={"username": username, "password": password},
            headers={"X-Application": app_key, "Accept": "application/json"},
            timeout=10,
        )
        token = login_resp.json().get("token")
        if not token:
            print("  [!] Betfair login failed", file=sys.stderr)
            return None
        auth = {"X-Application": app_key, "X-Authentication": token, "Content-Type": "application/json"}
        cat_resp = requests.post(
            "https://api.betfair.com/exchange/betting/rest/v1.0/listMarketCatalogue/",
            json={"filter": {"marketIds": [market_id]}, "marketProjection": ["RUNNER_DESCRIPTION"], "maxResults": 1},
            headers=auth, timeout=10,
        )
        runner_names = {}
        for cat in cat_resp.json():
            for runner in cat.get("runners", []):
                runner_names[runner["selectionId"]] = runner.get("runnerName", "")
        book_resp = requests.post(
            "https://api.betfair.com/exchange/betting/rest/v1.0/listMarketBook/",
            json={"marketIds": [market_id], "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}},
            headers=auth, timeout=10,
        )
        odds = {}
        for mkt in book_resp.json():
            for runner in mkt.get("runners", []):
                name = runner_names.get(runner["selectionId"], "")
                backs = runner.get("ex", {}).get("availableToBack", [])
                if backs:
                    price = backs[0]["price"]
                    if "labor" in name.lower() or "labour" in name.lower():
                        odds["Labor"] = price
                    elif "coalition" in name.lower() or "liberal" in name.lower():
                        odds["Coalition"] = price
                    else:
                        odds["Other"] = price
        if odds:
            return {"source": "Betfair Exch", "odds": odds}
    except Exception as e:
        print("  [!] Betfair scrape failed: {}".format(e), file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# DATA MANAGEMENT
# ---------------------------------------------------------------------------

def load_history():
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return []


def save_history(history):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(history, f, indent=2)


def add_snapshot(history, snapshot):
    date_str = snapshot["date"]
    history = [h for h in history if h["date"] != date_str]
    history.append(snapshot)
    history.sort(key=lambda x: x["date"])
    return history


def aggregate_odds(sources):
    agg = {}
    for outcome in OUTCOMES:
        prices = [s.get("odds", {}).get(outcome) for s in sources if s.get("odds", {}).get(outcome)]
        if prices:
            agg[outcome] = round(statistics.mean(prices), 3)
    return agg


# ---------------------------------------------------------------------------
# HTML GENERATION
# ---------------------------------------------------------------------------

def generate_html(history):
    latest = history[-1] if history else None
    weekly = []
    seen_weeks = set()
    for entry in reversed(history):
        dt = datetime.datetime.fromisoformat(entry["date"])
        year, week, _ = dt.isocalendar()
        wk = "{}-W{:02d}".format(year, week)
        if wk not in seen_weeks:
            seen_weeks.add(wk)
            weekly.append(entry)
    chart_dates, chart_labor, chart_coalition = [], [], []
    for entry in history:
        agg = entry.get("aggregate", {})
        if agg.get("Labor") and agg.get("Coalition"):
            chart_dates.append(entry["date"])
            chart_labor.append(agg["Labor"])
            chart_coalition.append(agg["Coalition"])

    updated_str = latest["date"] if latest else "No data"
    p = []

    p.append("""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Victorian State Election 2026 - Odds Tracker</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{--bg:#0c1117;--surface:#161b22;--surface2:#1c2129;--border:#2d333b;--text:#e6edf3;--text-dim:#8b949e;--labor-red:#e53935;--coalition-blue:#1e88e5;--other-grey:#8b949e;--accent:#f0b429}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'DM Sans',system-ui,sans-serif;background:var(--bg);color:var(--text);line-height:1.5}
.container{max-width:1100px;margin:0 auto;padding:2rem 1.5rem}
header{text-align:center;margin-bottom:3rem;padding-bottom:2rem;border-bottom:1px solid var(--border)}
header h1{font-size:1.8rem;font-weight:700;letter-spacing:-0.03em;margin-bottom:0.4rem}
.subtitle{color:var(--text-dim);font-size:0.95rem}
.updated{margin-top:0.8rem;font-family:'JetBrains Mono',monospace;font-size:0.78rem;color:var(--accent);opacity:0.8}
h2{font-size:0.85rem;font-weight:600;margin-bottom:1rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.08em}
.section{margin-bottom:3rem}
table{width:100%;border-collapse:collapse;font-size:0.88rem}
thead th{text-align:left;padding:0.6rem 0.8rem;border-bottom:2px solid var(--border);font-weight:600;color:var(--text-dim);font-size:0.78rem;text-transform:uppercase;letter-spacing:0.05em}
tbody td{padding:0.55rem 0.8rem;border-bottom:1px solid var(--border);font-family:'JetBrains Mono',monospace;font-size:0.82rem}
tbody tr:hover{background:var(--surface2)}
.col-source{font-family:'DM Sans',sans-serif;font-weight:500}
.col-labor{color:var(--labor-red)}.col-coalition{color:var(--coalition-blue)}.col-other{color:var(--other-grey)}
.best{font-weight:700;text-decoration:underline;text-underline-offset:3px}
.agg-row td{border-top:2px solid var(--border);font-weight:700;background:var(--surface)}
.chart-wrap{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:1.5rem}
.legend-custom{display:flex;gap:2rem;justify-content:center;margin-bottom:1rem;font-size:0.82rem;font-weight:500}
.legend-item{display:flex;align-items:center;gap:0.4rem}
.legend-dot{width:10px;height:10px;border-radius:50%}
.note{margin-top:1.5rem;padding:1rem;background:var(--surface);border-left:3px solid var(--accent);font-size:0.8rem;color:var(--text-dim);border-radius:0 4px 4px 0}
footer{text-align:center;padding:2rem 0;border-top:1px solid var(--border);color:var(--text-dim);font-size:0.75rem}
@media(max-width:700px){.container{padding:1rem}table{font-size:0.78rem}thead th,tbody td{padding:0.4rem}}
</style></head><body><div class="container">
<header><h1>Victorian State Election 2026</h1>
<div class="subtitle">Betting Odds Tracker - Payout on $1 AUD Bet</div>
<div class="updated">Last updated: """ + updated_str + """ AEST</div></header>
""")

    # Section 1
    p.append('<div class="section"><h2>1. Current Odds - All Sources</h2>')
    if latest and latest.get("sources"):
        sources = latest["sources"]
        agg = latest.get("aggregate", {})
        best = {}
        for oc in OUTCOMES:
            vals = [s["odds"].get(oc) for s in sources if s["odds"].get(oc)]
            if vals:
                best[oc] = max(vals)
        p.append('<table><thead><tr><th>Source</th><th>Labor</th><th>Coalition</th><th>Other</th></tr></thead><tbody>')
        for s in sorted(sources, key=lambda x: x["source"]):
            p.append("<tr>")
            p.append('<td class="col-source">{}</td>'.format(s["source"]))
            for oc, css in [("Labor", "col-labor"), ("Coalition", "col-coalition"), ("Other", "col-other")]:
                v = s["odds"].get(oc)
                if v:
                    cls = css + (" best" if v == best.get(oc) else "")
                    p.append('<td class="{}">${:.2f}</td>'.format(cls, v))
                else:
                    p.append('<td class="{}">-</td>'.format(css))
            p.append("</tr>")
        p.append('<tr class="agg-row"><td class="col-source">AGGREGATE (mean)</td>')
        for oc, css in [("Labor", "col-labor"), ("Coalition", "col-coalition"), ("Other", "col-other")]:
            v = agg.get(oc)
            p.append('<td class="{}">{}</td>'.format(css, "${:.2f}".format(v) if v else "-"))
        p.append("</tr></tbody></table>")
    else:
        p.append("<p>No data available yet.</p>")
    p.append("</div>")

    # Section 2
    p.append('<div class="section"><h2>2. Weekly Snapshot (Sunday 5pm AEST)</h2>')
    if weekly:
        p.append('<table><thead><tr><th>Week Ending</th><th>Labor</th><th>Coalition</th><th>Other</th><th>Sources</th></tr></thead><tbody>')
        for w in weekly:
            ag = w.get("aggregate", {})
            p.append("<tr>")
            p.append('<td class="col-source">{}</td>'.format(w["date"]))
            for oc, css in [("Labor", "col-labor"), ("Coalition", "col-coalition"), ("Other", "col-other")]:
                v = ag.get(oc)
                p.append('<td class="{}">{}</td>'.format(css, "${:.2f}".format(v) if v else "-"))
            p.append("<td>{}</td></tr>".format(len(w.get("sources", []))))
        p.append("</tbody></table>")
    else:
        p.append("<p>Not enough data yet.</p>")
    p.append("</div>")

    # Section 3
    p.append('<div class="section"><h2>3. Aggregate Odds Over Time</h2>')
    p.append('<div class="chart-wrap">')
    p.append('<div class="legend-custom"><div class="legend-item"><div class="legend-dot" style="background:var(--labor-red)"></div> Labor</div><div class="legend-item"><div class="legend-dot" style="background:var(--coalition-blue)"></div> Coalition</div></div>')
    p.append('<canvas id="oddsChart" height="90"></canvas></div></div>')
    p.append("<script>")
    p.append('new Chart(document.getElementById("oddsChart"),{type:"line",data:{')
    p.append("labels:{},datasets:[".format(json.dumps(chart_dates)))
    p.append('{label:"Labor $1 payout",data:' + json.dumps(chart_labor))
    p.append(',borderColor:"#e53935",backgroundColor:"rgba(229,57,53,0.08)",fill:true,tension:0.3,pointRadius:3,borderWidth:2.5},')
    p.append('{label:"Coalition $1 payout",data:' + json.dumps(chart_coalition))
    p.append(',borderColor:"#1e88e5",backgroundColor:"rgba(30,136,229,0.08)",fill:true,tension:0.3,pointRadius:3,borderWidth:2.5}')
    p.append(']},options:{responsive:true,interaction:{mode:"index",intersect:false},plugins:{legend:{display:false},')
    p.append('tooltip:{backgroundColor:"#161b22",borderColor:"#2d333b",borderWidth:1,callbacks:{label:function(c){return c.dataset.label+": $"+c.parsed.y.toFixed(2)}}}},')
    p.append('scales:{x:{grid:{color:"rgba(45,51,59,0.5)"},ticks:{color:"#8b949e",maxRotation:45,font:{family:"JetBrains Mono",size:10}}},')
    p.append('y:{title:{display:true,text:"Payout on $1 AUD bet",color:"#8b949e"},grid:{color:"rgba(45,51,59,0.5)"},')
    p.append('ticks:{color:"#8b949e",font:{family:"JetBrains Mono",size:11},callback:function(v){return "$"+v.toFixed(2)}},min:1.0}}}});')
    p.append("</script>")

    p.append("""<div class="note"><strong>Methodology:</strong> Decimal odds = total payout on a $1 AUD bet
(e.g. $1.80 = $0.80 profit + $1.00 stake). "Aggregate" = arithmetic mean across all sources.
Weekly snapshots use the closest daily reading to Sunday 5pm AEST. UK bookmaker fractional odds
(e.g. 4/5) are converted to decimal (4/5 + 1 = $1.80) — currency is irrelevant to the ratio.
<br><br><strong>Not for betting or wagering purposes.</strong> For political analysis and public interest only.</div>
<footer>Victorian State Election 2026 Odds Tracker &middot; Data from publicly listed bookmaker prices &middot; Not affiliated with any wagering operator</footer>
</div></body></html>""")

    with open(OUTPUT_HTML, "w") as f:
        f.write("".join(p))
    print("  [+] HTML written to {}".format(OUTPUT_HTML))


# ---------------------------------------------------------------------------
# DEMO DATA
# ---------------------------------------------------------------------------

def seed_demo_data():
    import random
    random.seed(42)
    history = []
    base = datetime.date(2025, 3, 30)
    lp, cp = 1.85, 2.30
    for d in range(56):
        dt = base + datetime.timedelta(days=d)
        lp = round(max(1.2, min(3.5, lp + random.gauss(0, 0.02))), 2)
        cp = round(max(1.2, min(4.0, cp + random.gauss(0, 0.025))), 2)
        op = round(random.uniform(8, 15), 2)
        sources = [
            {"source": "Sportsbet", "odds": {"Labor": lp, "Coalition": cp, "Other": op}},
            {"source": "bet365", "odds": {"Labor": round(lp - 0.03, 2), "Coalition": round(cp + 0.05, 2)}},
            {"source": "BetMGM", "odds": {"Labor": round(lp + 0.02, 2), "Coalition": cp}},
        ]
        history.append({"date": dt.isoformat(), "sources": sources, "aggregate": aggregate_odds(sources)})
    return history


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--seed-demo", action="store_true")
    args = parser.parse_args()

    history = load_history()

    if args.seed_demo:
        print("[*] Seeding demo data...")
        history = seed_demo_data()
        save_history(history)
        generate_html(history)
        return

    if not args.build_only:
        print("[*] Scraping odds sources...")
        all_sources = []

        for name, fn in [
            ("Sportsbet", scrape_sportsbet),
            ("TAB", scrape_tab),
            ("Oddschecker (UK books)", scrape_oddschecker),
            ("Betr", scrape_betr),
            ("Betfair Exchange", scrape_betfair),
        ]:
            print("  -> {}...".format(name))
            result = fn()
            if isinstance(result, list):
                all_sources.extend(result)
                print("    + {} book(s)".format(len(result)))
            elif result:
                all_sources.append(result)
                print("    + {}".format(result["odds"]))

        if all_sources:
            agg = aggregate_odds(all_sources)
            now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=10)))
            snapshot = {"date": now.strftime("%Y-%m-%d"), "sources": all_sources, "aggregate": agg}
            history = add_snapshot(history, snapshot)
            save_history(history)
            print("\n[+] Snapshot saved: {}".format(snapshot["date"]))
            print("    Aggregate: {}".format(agg))
        else:
            print("\n[!] No sources returned data.")
            if not history:
                return

    generate_html(history)


if __name__ == "__main__":
    main()
