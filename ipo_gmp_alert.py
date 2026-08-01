"""
IPO GMP Alert Agent
-------------------
Checks currently OPEN Mainboard IPOs (India) and their Grey Market Premium (GMP %).
Sends a Telegram alert (to one or more chats/channels) for every IPO with GMP >= GMP_THRESHOLD.

Data source: investorgain.com (unofficial GMP data — informational only, not investment advice)

Run manually:
    python ipo_gmp_alert.py
    python ipo_gmp_alert.py --debug     # prints raw scraped rows so you can fix selectors
"""

import os
import re
import sys
import argparse
import requests
from playwright.sync_api import sync_playwright

# ---------- CONFIG ----------
URL = "https://www.investorgain.com/report/live-ipo-gmp/331/ipo/"  # Mainboard-only GMP table
GMP_THRESHOLD = float(os.environ.get("GMP_THRESHOLD", "10"))       # percent

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
# The channel's chat ID (looks like -100xxxxxxxxxx). Telegram itself keeps track
# of who has joined the channel -- there's nothing for this script to persist.
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
# -----------------------------


def fetch_rows(debug=False):
    """Load the page with a real browser (data is JS-rendered) and pull table rows as text."""
    rows_data = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ))
        page.goto(URL, timeout=60000)

        # Wait for the data table to actually populate (it loads via AJAX after page load)
        page.wait_for_selector("table tbody tr", timeout=30000)
        page.wait_for_timeout(1500)  # small buffer for all rows to render

        headers = [h.strip() for h in page.locator("table thead th").all_inner_texts()]
        row_locators = page.locator("table tbody tr")
        count = row_locators.count()

        for i in range(count):
            cells = row_locators.nth(i).locator("td").all_inner_texts()
            cells = [c.strip().replace("\n", " ") for c in cells]
            if debug:
                print(f"ROW {i}: {cells}")
            rows_data.append({"headers": headers, "cells": cells, "raw_text": " | ".join(cells)})

        browser.close()
    return rows_data


def parse_ipo(row):
    """
    Extract IPO name, status (Open/Upcoming/Close/Listed) and GMP % from a row.
    Column layout can shift on the source site, so we match by content instead
    of a fixed column index -- more resilient to minor site changes.
    """
    text = row["raw_text"]
    cells = row["cells"]

    if not cells:
        return None

    name = cells[0].split("IPO")[0].strip() or cells[0].strip()

    status = None
    for s in ["Open", "Upcoming", "Close", "Listed"]:
        if re.search(rf"\b{s}\b", text, re.IGNORECASE):
            status = s
            break

    gmp_percent = None
    match = re.search(r"\(([-+]?\d+(?:\.\d+)?)\s*%\)", text)
    if match:
        gmp_percent = float(match.group(1))

    return {"name": name, "status": status, "gmp_percent": gmp_percent, "raw": text}


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
        lines.append(f"• <b>{ipo['name']}</b> — GMP {ipo['gmp_percent']}%")
    lines.append("")
    lines.append("⚠️ GMP is unofficial grey-market data, not investment advice. Verify before applying.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="print raw scraped rows")
    args = parser.parse_args()

    rows = fetch_rows(debug=args.debug)
    parsed = [parse_ipo(r) for r in rows]
    parsed = [p for p in parsed if p]

    if args.debug:
        print("\n--- PARSED ---")
        for p in parsed:
            print(p)

    open_ipos = [p for p in parsed if p["status"] == "Open"]
    hits = [p for p in open_ipos if p["gmp_percent"] is not None and p["gmp_percent"] >= GMP_THRESHOLD]
    hits.sort(key=lambda x: x["gmp_percent"], reverse=True)

    print(f"Found {len(open_ipos)} open mainboard IPO(s), {len(hits)} with GMP >= {GMP_THRESHOLD}%.")

    if hits:
        send_telegram(build_message(hits))
    else:
        print("No IPOs crossed the GMP threshold today. No alert sent.")
        # Uncomment below if you want a "nothing today" ping too:
        # send_telegram(f"No open mainboard IPOs with GMP >= {GMP_THRESHOLD}% today.")


if __name__ == "__main__":
    sys.exit(main())
