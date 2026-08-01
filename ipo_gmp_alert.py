"""
IPO GMP Alert Agent
-------------------
Checks currently OPEN Mainboard IPOs (India) and their Grey Market Premium (GMP %),
using TWO independent sources cross-checked against each other for reliability.
Sends a Telegram alert to your channel for every IPO with GMP >= GMP_THRESHOLD.

Sources:
  1. investorgain.com  (JS-rendered table, read via headless browser)
  2. ipowatch.in       (plain server-rendered HTML, read via requests)
If one source fails or is unreachable, the script carries on with the other and
says so in the logs. If BOTH fail, it sends a warning message instead of
silently doing nothing, so a broken scraper doesn't go unnoticed.

Data is unofficial grey-market info -- informational only, not investment advice.

Run manually:
    python ipo_gmp_alert.py
    python ipo_gmp_alert.py --debug     # prints raw scraped rows from both sources
"""

import os
import re
import sys
import argparse
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ---------- CONFIG ----------
GMP_THRESHOLD = float(os.environ.get("GMP_THRESHOLD", "10"))  # percent
MISMATCH_FLAG_THRESHOLD = 5.0  # percentage-point gap between sources worth flagging

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
# The channel's chat ID (looks like -100xxxxxxxxxx). Telegram itself keeps track
# of who has joined the channel -- there's nothing for this script to persist.
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

INVESTORGAIN_URL = "https://www.investorgain.com/report/live-ipo-gmp/331/ipo/"
IPOWATCH_URL = "https://ipowatch.in/ipo-grey-market-premium-latest-ipo-gmp/"
# -----------------------------


def normalize_name(name):
    """Loose key for matching the same IPO across two differently-formatted sources."""
    return re.sub(r"[^a-z0-9]", "", name.lower())[:20]


# ---------- SOURCE 1: investorgain.com (JS-rendered) ----------

def fetch_investorgain(debug=False):
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ))
            page.goto(INVESTORGAIN_URL, timeout=60000)
            page.wait_for_selector("table tbody tr", timeout=30000)
            page.wait_for_timeout(1500)

            row_locators = page.locator("table tbody tr")
            count = row_locators.count()

            for i in range(count):
                cells = row_locators.nth(i).locator("td").all_inner_texts()
                cells = [c.strip().replace("\n", " ") for c in cells]
                if not cells:
                    continue
                text = " | ".join(cells)
                if debug:
                    print(f"[investorgain] ROW {i}: {cells}")

                name = cells[0].split("IPO")[0].strip() or cells[0].strip()
                status = next((s for s in ["Open", "Upcoming", "Close", "Listed"]
                               if re.search(rf"\b{s}\b", text, re.IGNORECASE)), None)
                match = re.search(r"\(([-+]?\d+(?:\.\d+)?)\s*%\)", text)
                gmp_percent = float(match.group(1)) if match else None

                results.append({"name": name, "status": status, "gmp_percent": gmp_percent})

            browser.close()
        print(f"[investorgain] scraped {len(results)} rows.")
    except Exception as e:
        print(f"[investorgain] FAILED: {e}")
    return results


# ---------- SOURCE 2: ipowatch.in (static HTML) ----------

def fetch_ipowatch(debug=False):
    results = []
    try:
        resp = requests.get(IPOWATCH_URL, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        table = None
        for t in soup.find_all("table"):
            header_text = t.find("tr").get_text(" ", strip=True) if t.find("tr") else ""
            if "IPO Name" in header_text or "GMP" in header_text:
                table = t
                break

        if table is None:
            raise ValueError("could not locate the GMP table on the page")

        rows = table.find_all("tr")[1:]  # skip header row
        for r in rows:
            cells = [c.get_text(" ", strip=True) for c in r.find_all(["td", "th"])]
            if not cells:
                continue
            text = " | ".join(cells)
            if debug:
                print(f"[ipowatch] ROW: {cells}")

            name = cells[0].strip()
            is_mainboard = bool(re.search(r"\bMainboard\b", text, re.IGNORECASE)) and not re.search(r"SME", text, re.IGNORECASE)
            status = next((s for s in ["Open", "Upcoming", "Close", "Closed", "Listed"]
                           if re.search(rf"\b{s}\b", text, re.IGNORECASE)), None)
            if status == "Close":
                status = "Closed"
            match = re.search(r"\(([-+]?\d+(?:\.\d+)?)\s*%\)", text)
            gmp_percent = float(match.group(1)) if match else None

            results.append({
                "name": name,
                "status": status,
                "gmp_percent": gmp_percent,
                "is_mainboard": is_mainboard,
            })
        print(f"[ipowatch] scraped {len(results)} rows.")
    except Exception as e:
        print(f"[ipowatch] FAILED: {e}")
    return results


# ---------- CROSS-CHECK & MERGE ----------

def merge_sources(ig_rows, iw_rows):
    merged = {}

    for row in ig_rows:
        if row["status"] != "Open" or row["gmp_percent"] is None:
            continue
        key = normalize_name(row["name"])
        merged[key] = {"name": row["name"], "sources": {"investorgain": row["gmp_percent"]}}

    for row in iw_rows:
        if row["status"] != "Open" or row["gmp_percent"] is None or not row.get("is_mainboard"):
            continue
        key = normalize_name(row["name"])
        if key in merged:
            merged[key]["sources"]["ipowatch"] = row["gmp_percent"]
        else:
            merged[key] = {"name": row["name"], "sources": {"ipowatch": row["gmp_percent"]}}

    final = []
    for item in merged.values():
        values = list(item["sources"].values())
        avg = sum(values) / len(values)
        mismatch = len(values) == 2 and abs(values[0] - values[1]) >= MISMATCH_FLAG_THRESHOLD
        final.append({
            "name": item["name"],
            "gmp_percent": round(avg, 2),
            "num_sources": len(values),
            "mismatch": mismatch,
            "sources": item["sources"],
        })
    return final


# ---------- TELEGRAM ----------

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing — skipping send. Message was:\n", message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    if resp.status_code != 200:
        print("Telegram send failed:", resp.text)
    else:
        print("Telegram alert sent.")


def build_message(hits):
    lines = [f"<b>🚀 Open Mainboard IPOs with GMP ≥ {GMP_THRESHOLD}%</b>", ""]
    for ipo in hits:
        flag = " ⚠️ sources disagree" if ipo["mismatch"] else ""
        src_note = "1 source" if ipo["num_sources"] == 1 else "2 sources"
        lines.append(f"• <b>{ipo['name']}</b> — GMP {ipo['gmp_percent']}% ({src_note}){flag}")
    lines.append("")
    lines.append("⚠️ GMP is unofficial grey-market data, not investment advice. Verify before applying.")
    return "\n".join(lines)


# ---------- MAIN ----------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="print raw scraped rows from both sources")
    args = parser.parse_args()

    ig_rows = fetch_investorgain(debug=args.debug)
    iw_rows = fetch_ipowatch(debug=args.debug)

    if not ig_rows and not iw_rows:
        send_telegram(
            "⚠️ <b>IPO GMP Alert Agent</b>: both data sources failed today. "
            "No IPO check was possible. You may want to check the source sites manually, "
            "or the scrapers may need a small fix."
        )
        return

    if not ig_rows:
        print("Continuing with ipowatch.in only (investorgain unavailable).")
    if not iw_rows:
        print("Continuing with investorgain.com only (ipowatch unavailable).")

    merged = merge_sources(ig_rows, iw_rows)

    if args.debug:
        print("\n--- MERGED ---")
        for m in merged:
            print(m)

    hits = [m for m in merged if m["gmp_percent"] >= GMP_THRESHOLD]
    hits.sort(key=lambda x: x["gmp_percent"], reverse=True)

    print(f"Found {len(merged)} open mainboard IPO(s) total, {len(hits)} with GMP >= {GMP_THRESHOLD}%.")

    if hits:
        send_telegram(build_message(hits))
    else:
        print("No IPOs crossed the GMP threshold today. No alert sent.")


if __name__ == "__main__":
    sys.exit(main())
