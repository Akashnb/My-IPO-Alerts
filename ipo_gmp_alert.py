"""
IPO GMP Alert Agent
-------------------
Checks currently OPEN Mainboard IPOs (India) and their Grey Market Premium (GMP %),
using TWO independent sources cross-checked against each other for reliability.
Sends a Telegram alert to your channel for every IPO with GMP >= GMP_THRESHOLD,
showing GMP%, estimated per-share profit in Rs, day-over-day trend (▲/▼ with the
percentage-point change), and the subscription window (open/close dates + which
day it is today).

Sources:
  1. investorgain.com  (JS-rendered table, read via headless browser)
  2. ipowatch.in       (plain server-rendered HTML, read via requests)
The richer fields (profit in Rs) are pulled from ipowatch.in only, since its
table structure has been directly verified. investorgain.com is kept as a
second source purely for GMP % cross-checking.

STATE / MEMORY (state.json, committed back to the repo each run):
  - Powers day-over-day trend arrows (compares today's GMP% to the last run
    that saw this IPO).
  - Prevents duplicate alerts if the workflow runs more than once on the same
    calendar day (e.g. scheduled run + a manual test run).
  - Tracks consecutive scheduled runs that found zero open mainboard IPOs at
    all -- a sign the scraper itself may be silently broken (site redesign,
    etc.), not just a quiet market day -- and sends a one-time warning if that
    streak gets suspiciously long.

If one source fails or is unreachable, the script carries on with the other and
says so in the logs. If BOTH fail, it sends a warning message instead of
silently doing nothing.

Data is unofficial grey-market info -- informational only, not investment advice.

Run manually:
    python ipo_gmp_alert.py
    python ipo_gmp_alert.py --debug     # prints raw scraped rows and merged state
"""

import os
import re
import sys
import json
import time
import html
import argparse
import requests
from datetime import datetime, date, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ---------- CONFIG ----------
GMP_THRESHOLD = float(os.environ.get("GMP_THRESHOLD", "10"))  # percent
MISMATCH_FLAG_THRESHOLD = 5.0  # percentage-point gap between sources worth flagging
BREAKAGE_STREAK_DAYS = int(os.environ.get("BREAKAGE_STREAK_DAYS", "3"))  # consecutive zero-IPO scheduled runs before warning

# Grey market premiums this far outside normal range are almost certainly a
# parsing error (e.g. picked up the wrong number from the page), not a real
# GMP -- reject rather than trust. Generous bounds since GMP genuinely can be
# negative (discount) or occasionally very high for a hot IPO.
SANE_GMP_RANGE = (-50.0, 500.0)

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 3  # seconds; grows linearly with each retry (3s, 6s, 9s...)

STATE_RETENTION_DAYS = int(os.environ.get("STATE_RETENTION_DAYS", "60"))  # prune history untouched this long

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

INVESTORGAIN_URL = "https://www.investorgain.com/report/live-ipo-gmp/331/ipo/"
IPOWATCH_URL = "https://ipowatch.in/ipo-grey-market-premium-latest-ipo-gmp/"

STATE_FILE = os.environ.get("STATE_FILE", "state.json")
IST = ZoneInfo("Asia/Kolkata")
# -----------------------------

DATE_RANGE_RE = re.compile(r"\b(\d{1,2})-(\d{1,2})\s+([A-Za-z]+)\b")
RUPEE_RE = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)")
LOT_SIZE_PATTERNS = [
    re.compile(r"lot size[\w\s]{0,25}?\bis (\d+)\s*shares", re.IGNORECASE),
    re.compile(r"lot size of (\d+)\s*shares", re.IGNORECASE),
    re.compile(r"market lot is (\d+)\s*shares", re.IGNORECASE),
    re.compile(r"bid for a minimum of (\d+)\s*(?:equity\s*)?shares", re.IGNORECASE),
    re.compile(r"minimum lot is (\d+)\s*shares", re.IGNORECASE),
]


def today_ist():
    return datetime.now(IST).date()


def normalize_name(name):
    """Loose key for matching the same IPO across sources and across days."""
    return re.sub(r"[^a-z0-9]", "", name.lower())[:20]


def extract_status(text, options):
    """Shared status-parsing logic for both scrapers -- avoids the same regex loop being copy-pasted twice."""
    status = next((s for s in options if re.search(rf"\b{s}\b", text, re.IGNORECASE)), None)
    return "Closed" if status == "Close" else status


def sane_gmp_or_none(gmp_percent):
    """
    Rejects a parsed GMP% if it falls outside plausible real-world bounds.
    This is a deliberate backstop, not a nice-to-have: investorgain.com's
    exact table structure has never been directly verified (it's JS-rendered,
    and none of the tools available for building this could execute JS to
    inspect it) -- Playwright, which the live script uses, can render it, but
    that means correctness here rests on trusting an unverified assumption.
    A value like "4500%" or "-9000%" almost certainly means a parsing error
    (wrong number picked up from the page), not a real GMP -- so it's
    discarded rather than silently fed into an average or an alert.
    """
    if gmp_percent is None:
        return None
    lo, hi = SANE_GMP_RANGE
    if lo <= gmp_percent <= hi:
        return gmp_percent
    print(f"[sanity-check] Rejected implausible GMP value: {gmp_percent}% (outside {lo} to {hi})")
    return None


def retry(fn, attempts=RETRY_ATTEMPTS, base_delay=RETRY_BASE_DELAY, label=""):
    """
    Retries a zero-arg callable on any exception, with linear backoff.
    Returns the callable's result on success. On final failure, re-raises the
    last exception -- the caller's existing try/except is what turns that
    into a logged "[source] FAILED" rather than crashing the whole run.
    """
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            print(f"[retry] {label} attempt {attempt}/{attempts} failed: {e}")
            if attempt < attempts:
                time.sleep(base_delay * attempt)
    raise last_exc


# ---------- STATE (persisted to state.json, committed back to the repo) ----------

def default_state():
    return {
        "date": None,           # IST date string this state block belongs to
        "alerted_ipos": [],     # normalized IPO keys already alerted today -- dedup
        "gmp_history": {},      # {ipo_key: last-seen gmp_percent} -- powers trend arrows
        "lot_size_cache": {},   # {ipo_key: lot_size} -- never expires, lot size doesn't change per IPO
        "zero_ipo_streak": 0,   # consecutive SCHEDULED runs with 0 open mainboard IPOs found
        "breakage_warned": False,
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        state = {}
    else:
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[state] Could not read {STATE_FILE} ({e}) -- starting fresh.")
            state = {}

    merged = default_state()
    merged.update(state)

    today = str(today_ist())
    if merged.get("date") != today:
        # New calendar day (IST) -- dedup list resets, everything else carries forward
        merged["date"] = today
        merged["alerted_ipos"] = []
    return merged


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        print(f"[state] Saved {STATE_FILE}.")
    except OSError as e:
        print(f"[state] FAILED to save {STATE_FILE}: {e}")


def get_previous_gmp(gmp_history, key):
    """
    Reads gmp_history entries in the current {'gmp_percent':..., 'last_seen':...}
    format. Also tolerates the older flat-float format ({key: 29.41}) that
    existed before pruning was added, so an existing state.json from before
    this change doesn't break -- it just loses one day of trend continuity
    for entries in the old format, rather than crashing.
    """
    entry = gmp_history.get(key)
    if isinstance(entry, dict):
        return entry.get("gmp_percent")
    return None  # missing, or legacy flat-float format we don't try to interpret


def get_cached_lot_size(lot_size_cache, key):
    """Same backward-compatibility story as get_previous_gmp, for the lot-size cache."""
    entry = lot_size_cache.get(key)
    if isinstance(entry, dict):
        return entry.get("lot_size")
    if isinstance(entry, int):
        return entry  # legacy format from before caching had timestamps
    return None


def prune_state(state, retention_days=STATE_RETENTION_DAYS):
    """
    Drops gmp_history/lot_size_cache entries not touched in `retention_days`.
    An IPO that closed and listed months ago has no reason to stay in memory
    forever -- this keeps state.json from growing without bound as more IPOs
    pass through over time. Entries in the legacy (pre-timestamp) format are
    left alone rather than guessed at or deleted.
    """
    cutoff = today_ist() - timedelta(days=retention_days)
    removed = 0

    for key in list(state["gmp_history"].keys()):
        entry = state["gmp_history"][key]
        if isinstance(entry, dict):
            last_seen = entry.get("last_seen")
            if last_seen and date.fromisoformat(last_seen) < cutoff:
                del state["gmp_history"][key]
                removed += 1

    for key in list(state["lot_size_cache"].keys()):
        entry = state["lot_size_cache"][key]
        if isinstance(entry, dict):
            cached_at = entry.get("cached_at")
            if cached_at and date.fromisoformat(cached_at) < cutoff:
                del state["lot_size_cache"][key]
                removed += 1

    if removed:
        print(f"[state] Pruned {removed} entries untouched for {retention_days}+ days.")


# ---------- Extraction helpers ----------

def extract_date_str(text):
    m = DATE_RANGE_RE.search(text)
    return m.group(0) if m else None


def extract_rupee_amounts(text):
    return [m.replace(",", "") for m in RUPEE_RE.findall(text)]


def extract_lot_size(text):
    for pattern in LOT_SIZE_PATTERNS:
        m = pattern.search(text)
        if m:
            return int(m.group(1))
    return None


def fetch_lot_size(session, detail_url):
    """
    Fetches an individual IPO's detail page and extracts its lot size (shares
    per application). Only called for IPOs not already in the lot-size cache
    (see resolve_lot_sizes), so in practice this runs once per IPO ever, not
    once per alert. Returns None (never raises) if the page or the pattern
    isn't found -- a missing lot size degrades to a per-share profit line.
    """
    if not detail_url:
        return None

    def attempt():
        resp = session.get(detail_url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        text = BeautifulSoup(resp.text, "html.parser").get_text(" ", strip=True)
        return extract_lot_size(text)

    try:
        lot_size = retry(attempt, attempts=2, label=f"lot-size ({detail_url})")
        if lot_size is None:
            print(f"[lot-size] Could not find a lot size on {detail_url}")
        return lot_size
    except Exception as e:
        print(f"[lot-size] FAILED fetching {detail_url} after 2 attempts: {e}")
        return None


def resolve_lot_sizes(new_hits, lot_size_cache, session):
    """
    Fills in ipo['lot_size'] for every hit, using the persisted cache first
    (lot size never changes for a given IPO, so once known it's known forever)
    and fetching only the ones we haven't seen before -- in parallel, since
    each fetch is an independent HTTP request.
    """
    uncached = [h for h in new_hits if get_cached_lot_size(lot_size_cache, h["key"]) is None]
    if uncached:
        with ThreadPoolExecutor(max_workers=4) as pool:
            fetched = list(pool.map(lambda h: fetch_lot_size(session, h.get("detail_url")), uncached))
        for h, lot_size in zip(uncached, fetched):
            if lot_size:
                lot_size_cache[h["key"]] = {"lot_size": lot_size, "cached_at": str(today_ist())}

    for h in new_hits:
        h["lot_size"] = get_cached_lot_size(lot_size_cache, h["key"])


def parse_ipo_date_range(date_str, today=None):
    """
    Parses strings like '30-3 August' (open day - close day, close month) into
    (open_date, close_date), handling the window crossing a month boundary.
    """
    if not date_str:
        return None
    m = DATE_RANGE_RE.search(date_str)
    if not m:
        return None
    open_day, close_day, close_month_name = m.groups()
    open_day, close_day = int(open_day), int(close_day)
    today = today or today_ist()

    try:
        close_month = datetime.strptime(close_month_name[:3], "%b").month
    except ValueError:
        return None

    close_year = today.year
    if close_day >= open_day:
        open_month, open_year = close_month, close_year
    else:
        open_month = close_month - 1
        open_year = close_year
        if open_month == 0:
            open_month, open_year = 12, close_year - 1

    try:
        open_date = date(open_year, open_month, open_day)
        close_date = date(close_year, close_month, close_day)
    except ValueError:
        return None
    return open_date, close_date


def get_window_info(date_str, today=None):
    parsed = parse_ipo_date_range(date_str, today=today)
    if not parsed:
        return None
    open_date, close_date = parsed
    today = today or today_ist()

    day_label = None
    if open_date <= today <= close_date:
        day_label = "Last day to apply" if today == close_date else f"Day {(today - open_date).days + 1}"

    formatted_range = f"{open_date.strftime('%d %b')} – {close_date.strftime('%d %b')}"
    return {"day_label": day_label, "formatted_range": formatted_range}


def format_trend(current_gmp, previous_gmp):
    """Returns e.g. '▲ 2.30%' / '▼ 1.10%' / '→ 0.00%', or None if no prior data to compare."""
    if previous_gmp is None:
        return None
    delta = current_gmp - previous_gmp
    if delta > 0:
        arrow = "▲"
    elif delta < 0:
        arrow = "▼"
    else:
        arrow = "→"
    return f"{arrow} {abs(delta):.2f}%"


# ---------- SOURCE 1: investorgain.com (JS-rendered) ----------

def fetch_investorgain(debug=False):
    def attempt():
        results = []
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
                status = extract_status(text, ["Open", "Upcoming", "Close", "Listed"])
                gmp_match = re.search(r"\(([-+]?\d+(?:\.\d+)?)\s*%\)", text)
                gmp_percent = sane_gmp_or_none(float(gmp_match.group(1))) if gmp_match else None
                date_str = extract_date_str(text)

                results.append({
                    "name": name, "status": status,
                    "gmp_percent": gmp_percent, "date_str": date_str,
                })

            browser.close()
        return results

    try:
        results = retry(attempt, label="investorgain")
        print(f"[investorgain] scraped {len(results)} rows.")
        return results
    except Exception as e:
        print(f"[investorgain] FAILED after {RETRY_ATTEMPTS} attempts: {e}")
        return []


# ---------- SOURCE 2: ipowatch.in (static HTML) ----------

def fetch_ipowatch(session, debug=False):
    def attempt():
        resp = session.get(IPOWATCH_URL, timeout=30, headers={
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

        results = []
        rows = table.find_all("tr")[1:]
        for r in rows:
            cells = [c.get_text(" ", strip=True) for c in r.find_all(["td", "th"])]
            if not cells:
                continue
            text = " | ".join(cells)
            if debug:
                print(f"[ipowatch] ROW: {cells}")

            name = cells[0].strip()
            is_mainboard = bool(re.search(r"\bMainboard\b", text, re.IGNORECASE)) and not re.search(r"SME", text, re.IGNORECASE)
            status = extract_status(text, ["Open", "Upcoming", "Close", "Closed", "Listed"])
            gmp_match = re.search(r"\(([-+]?\d+(?:\.\d+)?)\s*%\)", text)
            gmp_percent = sane_gmp_or_none(float(gmp_match.group(1))) if gmp_match else None
            date_str = extract_date_str(text)

            amounts = extract_rupee_amounts(text)
            gmp_rs = amounts[0] if len(amounts) >= 1 else None
            price_band = amounts[1] if len(amounts) >= 2 else None
            est_listing = amounts[2] if len(amounts) >= 3 else None

            link_tag = r.find("a", href=True)
            detail_url = urljoin(IPOWATCH_URL, link_tag["href"]) if link_tag else None

            results.append({
                "name": name,
                "status": status,
                "gmp_percent": gmp_percent,
                "is_mainboard": is_mainboard,
                "date_str": date_str,
                "gmp_rs": gmp_rs,
                "price_band": price_band,
                "est_listing": est_listing,
                "detail_url": detail_url,
            })
        return results

    try:
        results = retry(attempt, label="ipowatch")
        print(f"[ipowatch] scraped {len(results)} rows.")
        return results
    except Exception as e:
        print(f"[ipowatch] FAILED after {RETRY_ATTEMPTS} attempts: {e}")
        return []


# ---------- CROSS-CHECK & MERGE ----------

def merge_sources(ig_rows, iw_rows, gmp_history):
    merged = {}

    for row in ig_rows:
        if row["status"] != "Open" or row["gmp_percent"] is None:
            continue
        key = normalize_name(row["name"])
        merged[key] = {
            "name": row["name"],
            "sources": {"investorgain": row["gmp_percent"]},
            "date_str": row.get("date_str"),
            "gmp_rs": None, "price_band": None, "est_listing": None,
        }

    for row in iw_rows:
        if row["status"] != "Open" or row["gmp_percent"] is None or not row.get("is_mainboard"):
            continue
        key = normalize_name(row["name"])
        if key in merged:
            merged[key]["sources"]["ipowatch"] = row["gmp_percent"]
        else:
            merged[key] = {"name": row["name"], "sources": {"ipowatch": row["gmp_percent"]}}
        merged[key]["date_str"] = row.get("date_str") or merged[key].get("date_str")
        merged[key]["gmp_rs"] = row.get("gmp_rs")
        merged[key]["price_band"] = row.get("price_band")
        merged[key]["est_listing"] = row.get("est_listing")
        merged[key]["detail_url"] = row.get("detail_url")

    final = []
    for key, item in merged.items():
        values = list(item["sources"].values())
        avg = round(sum(values) / len(values), 2)
        mismatch = len(values) == 2 and abs(values[0] - values[1]) >= MISMATCH_FLAG_THRESHOLD
        window = get_window_info(item.get("date_str")) or {}
        final.append({
            "key": key,
            "name": item["name"],
            "gmp_percent": avg,
            "mismatch": mismatch,
            "gmp_rs": item.get("gmp_rs"),
            "price_band": item.get("price_band"),
            "est_listing": item.get("est_listing"),
            "detail_url": item.get("detail_url"),
            "trend": format_trend(avg, get_previous_gmp(gmp_history, key)),
            "day_label": window.get("day_label"),
            "formatted_range": window.get("formatted_range"),
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
        safe_name = html.escape(ipo["name"])
        lines.append(f"<b>📌 {safe_name}</b>")

        gmp_line = f"• GMP: {ipo['gmp_percent']}%"
        if ipo.get("trend"):
            gmp_line += f" {ipo['trend']}"
        else:
            gmp_line += " 🆕 new"
        if ipo["mismatch"]:
            gmp_line += " ⚠️ sources disagree"
        lines.append(gmp_line)

        if ipo.get("price_band"):
            lines.append(f"• Issue Price: ₹{ipo['price_band']}")

        if ipo.get("total_profit") is not None:
            lines.append(
                f"• Total Profit: ₹{ipo['total_profit']:,.0f} "
                f"for 1 lot ({ipo['lot_size']} shares, ₹{ipo['total_investment']:,.0f} invested)"
            )
        elif ipo.get("gmp_rs"):
            # Lot size wasn't found on the detail page this run -- degrade to
            # per-share rather than showing nothing, but say so explicitly.
            lines.append(f"• Profit: ₹{ipo['gmp_rs']}/share (lot size unavailable, showing per-share)")

        if ipo.get("formatted_range") or ipo.get("day_label"):
            parts = []
            if ipo.get("formatted_range"):
                parts.append(ipo["formatted_range"])
            if ipo.get("day_label"):
                parts.append(f"({ipo['day_label']})")
            lines.append("• Window: " + " ".join(parts))

        lines.append("")

    lines.append("⚠️ GMP is unofficial grey-market data, not investment advice. Verify before applying.")
    return "\n".join(lines)


# ---------- MAIN ----------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    is_scheduled_run = os.environ.get("GITHUB_EVENT_NAME", "schedule") == "schedule"
    state = load_state()
    session = requests.Session()  # reused across all ipowatch.in calls this run

    ig_rows = fetch_investorgain(debug=args.debug)
    iw_rows = fetch_ipowatch(session, debug=args.debug)

    if not ig_rows and not iw_rows:
        send_telegram(
            "⚠️ <b>IPO GMP Alert Agent</b>: both data sources failed today. "
            "No IPO check was possible. You may want to check the source sites manually, "
            "or the scrapers may need a small fix."
        )
        prune_state(state)
        save_state(state)
        return

    if not ig_rows:
        print("Continuing with ipowatch.in only (investorgain unavailable).")
    if not iw_rows:
        print("Continuing with investorgain.com only (ipowatch unavailable) -- profit/window data will be missing this run.")

    merged = merge_sources(ig_rows, iw_rows, state["gmp_history"])

    if args.debug:
        print("\n--- MERGED ---")
        for m in merged:
            print(m)

    # --- Silent-breakage detection: N consecutive SCHEDULED runs finding 0 open mainboard IPOs ---
    if is_scheduled_run:
        if len(merged) == 0:
            state["zero_ipo_streak"] += 1
        else:
            state["zero_ipo_streak"] = 0
            state["breakage_warned"] = False

        if state["zero_ipo_streak"] >= BREAKAGE_STREAK_DAYS and not state["breakage_warned"]:
            send_telegram(
                f"⚠️ <b>IPO GMP Alert Agent</b>: 0 open mainboard IPOs found for "
                f"{state['zero_ipo_streak']} scheduled runs in a row. This can happen in a "
                f"genuinely quiet market, but it can also mean a source site changed its layout "
                f"and the scraper needs a fix. Worth a quick manual check."
            )
            state["breakage_warned"] = True

    # --- Update GMP history for every open mainboard IPO seen today (powers tomorrow's trend) ---
    for m in merged:
        state["gmp_history"][m["key"]] = {"gmp_percent": m["gmp_percent"], "last_seen": str(today_ist())}

    # --- Threshold filter + same-day dedup ---
    hits = [m for m in merged if m["gmp_percent"] >= GMP_THRESHOLD]
    hits.sort(key=lambda x: x["gmp_percent"], reverse=True)

    already_alerted = set(state["alerted_ipos"])
    new_hits = [h for h in hits if h["key"] not in already_alerted]
    skipped = len(hits) - len(new_hits)

    print(f"Found {len(merged)} open mainboard IPO(s) total, {len(hits)} with GMP >= {GMP_THRESHOLD}% "
          f"({skipped} already alerted today, {len(new_hits)} new).")

    if new_hits:
        resolve_lot_sizes(new_hits, state["lot_size_cache"], session)
        for h in new_hits:
            if h["lot_size"] and h.get("gmp_rs") and h.get("price_band"):
                h["total_profit"] = int(h["lot_size"]) * float(h["gmp_rs"])
                h["total_investment"] = int(h["lot_size"]) * float(h["price_band"])
            else:
                h["total_profit"] = None
                h["total_investment"] = None

        send_telegram(build_message(new_hits))
        state["alerted_ipos"] = list(already_alerted | {h["key"] for h in new_hits})
    else:
        print("No new IPOs to alert (either none crossed the threshold, or all were already sent today).")

    prune_state(state)
    save_state(state)


if __name__ == "__main__":
    sys.exit(main())
