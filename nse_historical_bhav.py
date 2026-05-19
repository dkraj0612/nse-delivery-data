"""
nse_historical_bhav.py
======================
Downloads historical NSE Equity Bhav Copy (end-of-day) CSV files
for any user-specified date range.

Usage
-----
1. Edit START_DATE / END_DATE constants below, then run:
       python nse_historical_bhav.py

2. Or pass dates on the command line:
       python nse_historical_bhav.py --start 2023-01-01 --end 2023-12-31

3. Override the output root folder:
       python nse_historical_bhav.py --start 2024-01-01 --end 2024-06-30 --out /data/bhav
"""

# ─────────────────────────────────────────────────────────────
#  USER CONFIGURATION  ← edit these if not using CLI arguments
# ─────────────────────────────────────────────────────────────
START_DATE = "2023-01-01"   # YYYY-MM-DD
END_DATE   = "2023-12-31"   # YYYY-MM-DD
OUTPUT_ROOT = "./HistoricalBhavCopy/NSE"  # base output folder


# ─────────────────────────────────────────────────────────────
#  IMPORTS
# ─────────────────────────────────────────────────────────────
import os
import io
import sys
import time
import random
import zipfile
import logging
import argparse
from datetime import date, timedelta
from pathlib import Path

import requests

# ─────────────────────────────────────────────────────────────
#  LOGGING  (console + file)
# ─────────────────────────────────────────────────────────────
LOG_FILE = "bhav_download.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  USER-AGENT POOL  (rotated per request to avoid fingerprinting)
# ─────────────────────────────────────────────────────────────
USER_AGENTS = [
    # Chrome on Windows
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/124.0.0.0 Safari/537.36"),
    # Chrome on macOS
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
     "AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/123.0.0.0 Safari/537.36"),
    # Firefox on Windows
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
     "Gecko/20100101 Firefox/125.0"),
    # Edge on Windows
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"),
    # Safari on macOS
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) "
     "AppleWebKit/605.1.15 (KHTML, like Gecko) "
     "Version/17.4 Safari/605.1.15"),
]

ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-IN,en;q=0.9,hi;q=0.8",
    "en-US,en;q=0.8,hi;q=0.6",
]


# ─────────────────────────────────────────────────────────────
#  SESSION FACTORY
# ─────────────────────────────────────────────────────────────
def make_session() -> requests.Session:
    """Create a requests.Session with rotating browser-like headers."""
    session = requests.Session()
    _refresh_headers(session)
    # Persist cookies across requests (important for NSE's CDN)
    session.cookies.clear()
    return session


def _refresh_headers(session: requests.Session) -> None:
    """Rotate User-Agent and Accept-Language for the next request."""
    session.headers.update({
        "User-Agent":       random.choice(USER_AGENTS),
        "Accept":           ("text/html,application/xhtml+xml,application/xml;"
                             "q=0.9,image/avif,image/webp,*/*;q=0.8"),
        "Accept-Language":  random.choice(ACCEPT_LANGUAGES),
        "Accept-Encoding":  "gzip, deflate, br",
        "Connection":       "keep-alive",
        "DNT":              "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":   "document",
        "Sec-Fetch-Mode":   "navigate",
        "Sec-Fetch-Site":   "none",
        "Sec-Fetch-User":   "?1",
        "Cache-Control":    "max-age=0",
    })


# ─────────────────────────────────────────────────────────────
#  NSE SESSION WARM-UP
#  NSE's Akamai CDN requires cookies from the homepage before
#  serving archive files.  Without this, most requests 403.
# ─────────────────────────────────────────────────────────────
def warm_up_nse(session: requests.Session) -> bool:
    """
    Visit NSE homepage to seed cookies.
    Returns True on success, False on failure (script continues either way).
    """
    log.info("Warming up NSE session (acquiring cookies)…")
    try:
        r = session.get("https://www.nseindia.com", timeout=30)
        r.raise_for_status()
        # Also visit the historical data page to pick up any extra cookies
        time.sleep(random.uniform(1.5, 2.5))
        session.headers["Referer"] = "https://www.nseindia.com/"
        r2 = session.get(
            "https://www.nseindia.com/market-data/all-reports",
            timeout=30
        )
        log.info("NSE warm-up done  (status %s | cookies: %d)",
                 r2.status_code, len(session.cookies))
        return True
    except Exception as exc:
        log.warning("NSE warm-up failed (will still attempt downloads): %s", exc)
        return False


# ─────────────────────────────────────────────────────────────
#  URL + PATH BUILDERS
# ─────────────────────────────────────────────────────────────
def nse_zip_filename(d: date) -> str:
    """
    NSE uses uppercase 3-letter month abbreviations in filenames.
    e.g.  date(2023, 5, 5)  →  'cm05MAY2023bhav.csv.zip'
    """
    return f"cm{d.strftime('%d').upper()}{d.strftime('%b').upper()}{d.year}bhav.csv.zip"


def nse_csv_filename(d: date) -> str:
    """Same but for the inner CSV file name inside the ZIP."""
    return f"cm{d.strftime('%d').upper()}{d.strftime('%b').upper()}{d.year}bhav.csv"


def nse_url(d: date) -> str:
    """
    NSE historical archive URL pattern:
    https://nsearchives.nseindia.com/content/historical/EQUITIES/YYYY/MMM/cmDDMMMYYYYbhav.csv.zip
    MMM is 3-letter uppercase month (JAN, FEB, …, DEC).
    """
    month_upper = d.strftime("%b").upper()   # MAY, JUN, etc.
    return (
        f"https://nsearchives.nseindia.com/content/historical/"
        f"EQUITIES/{d.year}/{month_upper}/{nse_zip_filename(d)}"
    )


def output_csv_path(d: date, root: str) -> Path:
    """
    Target path:  <root>/<YYYY>/cmDDMMMYYYYbhav.csv
    Year sub-folders keep large date ranges manageable.
    """
    year_dir = Path(root) / str(d.year)
    year_dir.mkdir(parents=True, exist_ok=True)
    return year_dir / nse_csv_filename(d)


# ─────────────────────────────────────────────────────────────
#  DOWNLOAD + EXTRACT  (single day)
# ─────────────────────────────────────────────────────────────
class DownloadResult:
    """Simple enum-like result codes for one day's download attempt."""
    DOWNLOADED  = "DOWNLOADED"    # fresh download, saved OK
    SKIPPED     = "SKIPPED"       # file already existed on disk
    HOLIDAY     = "HOLIDAY"       # HTTP 404 → market holiday / no data
    ERROR       = "ERROR"         # unexpected HTTP error or I/O problem


def download_day(
    session: requests.Session,
    d: date,
    output_root: str,
) -> str:
    """
    Download, extract, and save the Bhav Copy CSV for a single date.
    Returns one of the DownloadResult constants.
    """
    dest_csv = output_csv_path(d, output_root)

    # ── 1. Skip if already on disk (resume support) ──────────
    if dest_csv.exists():
        log.info("  [SKIP]      %s  (already downloaded)", dest_csv.name)
        return DownloadResult.SKIPPED

    url = nse_url(d)
    log.info("  [FETCH]     %s", url)

    # ── 2. Rotate headers so each request looks slightly different ──
    _refresh_headers(session)
    session.headers["Referer"] = "https://www.nseindia.com/market-data/all-reports"

    # ── 3. Make the HTTP request ──────────────────────────────
    try:
        resp = session.get(url, timeout=45, stream=True)
    except requests.exceptions.ConnectionError as exc:
        log.error("  [ERROR]     Connection error for %s: %s", d, exc)
        return DownloadResult.ERROR
    except requests.exceptions.Timeout:
        log.error("  [ERROR]     Timed out for %s", d)
        return DownloadResult.ERROR

    # ── 4. Handle HTTP status codes ───────────────────────────
    if resp.status_code == 404:
        log.info("  [HOLIDAY]   %s → 404 (market holiday or no data)", d.strftime("%d-%b-%Y"))
        return DownloadResult.HOLIDAY

    if resp.status_code == 403:
        log.warning("  [BLOCKED]   %s → 403 Forbidden. Session may need re-warming.", d)
        return DownloadResult.ERROR

    if resp.status_code != 200:
        log.error("  [ERROR]     %s → HTTP %s", d, resp.status_code)
        return DownloadResult.ERROR

    # ── 5. Read ZIP bytes in memory ───────────────────────────
    zip_bytes = resp.content

    if len(zip_bytes) < 100:          # sanity check – valid ZIPs are never this small
        log.warning("  [ERROR]     Response too small (%d bytes) for %s – skipping.",
                    len(zip_bytes), d)
        return DownloadResult.ERROR

    # ── 6. Extract the inner CSV ──────────────────────────────
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                log.error("  [ERROR]     No CSV found inside ZIP for %s", d)
                return DownloadResult.ERROR

            inner_name = csv_names[0]
            raw_csv    = zf.read(inner_name)
    except zipfile.BadZipFile:
        log.error("  [ERROR]     Bad ZIP received for %s (HTML redirect?)", d)
        return DownloadResult.ERROR

    # ── 7. Write to disk ──────────────────────────────────────
    dest_csv.write_bytes(raw_csv)
    log.info("  [SAVED]     %s  (%d KB)", dest_csv, len(raw_csv) // 1024)
    return DownloadResult.DOWNLOADED


# ─────────────────────────────────────────────────────────────
#  DATE RANGE ITERATOR  (weekdays only)
# ─────────────────────────────────────────────────────────────
def weekdays_between(start: date, end: date):
    """Yield every Monday–Friday between start and end (inclusive)."""
    current = start
    while current <= end:
        if current.weekday() < 5:   # 0=Mon … 4=Fri
            yield current
        current += timedelta(days=1)


# ─────────────────────────────────────────────────────────────
#  MAIN ORCHESTRATOR
# ─────────────────────────────────────────────────────────────
def run(start: date, end: date, output_root: str) -> None:

    # Collect all target weekdays
    all_days = list(weekdays_between(start, end))
    total    = len(all_days)

    log.info("=" * 60)
    log.info("NSE Historical Bhav Copy Downloader")
    log.info("  Date range : %s → %s", start, end)
    log.info("  Weekdays   : %d", total)
    log.info("  Output root: %s", Path(output_root).resolve())
    log.info("=" * 60)

    if total == 0:
        log.warning("No weekdays in the given range. Nothing to do.")
        return

    # Counters
    stats = {r: 0 for r in (
        DownloadResult.DOWNLOADED,
        DownloadResult.SKIPPED,
        DownloadResult.HOLIDAY,
        DownloadResult.ERROR,
    )}

    session = make_session()
    warm_up_nse(session)

    # ── Session re-warm schedule ──────────────────────────────
    # NSE cookies expire after roughly 30 requests; re-warm every 25.
    REWARM_EVERY = 25

    for idx, day in enumerate(all_days, start=1):

        # Re-warm session periodically
        if idx > 1 and (idx - 1) % REWARM_EVERY == 0:
            log.info("── Re-warming NSE session (request %d / %d) ──", idx, total)
            warm_up_nse(session)

        log.info("[%4d / %4d]  %s", idx, total, day.strftime("%A, %d %b %Y"))

        result = download_day(session, day, output_root)
        stats[result] += 1

        # ── Rate limiting ─────────────────────────────────────
        # Always sleep between requests.
        # Use a longer delay if NSE returned an error (possible rate-limit signal).
        if result == DownloadResult.DOWNLOADED:
            delay = random.uniform(1.5, 3.5)
        elif result == DownloadResult.ERROR:
            delay = random.uniform(5.0, 10.0)   # back off on errors
        else:
            delay = random.uniform(0.5, 1.5)    # short delay for skips/holidays

        log.debug("  Sleeping %.1f s…", delay)
        time.sleep(delay)

    # ── Final summary ─────────────────────────────────────────
    log.info("")
    log.info("=" * 60)
    log.info("DOWNLOAD COMPLETE")
    log.info("  ✅  Downloaded : %d", stats[DownloadResult.DOWNLOADED])
    log.info("  ⏭️  Skipped    : %d  (already on disk)",
             stats[DownloadResult.SKIPPED])
    log.info("  📅  Holidays   : %d  (404 – no trading data)",
             stats[DownloadResult.HOLIDAY])
    log.info("  ❌  Errors     : %d  (see log for details)",
             stats[DownloadResult.ERROR])
    log.info("  Log saved to  : %s", Path(LOG_FILE).resolve())
    log.info("=" * 60)


# ─────────────────────────────────────────────────────────────
#  CLI ARGUMENT PARSER
# ─────────────────────────────────────────────────────────────
def parse_args() -> tuple[date, date, str]:
    """
    Parse CLI arguments.  Falls back to the module-level constants
    (START_DATE, END_DATE, OUTPUT_ROOT) if no CLI args are given.
    """
    parser = argparse.ArgumentParser(
        description="Download historical NSE Equity Bhav Copy CSV files.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python nse_historical_bhav.py\n"
            "  python nse_historical_bhav.py --start 2022-01-01 --end 2022-12-31\n"
            "  python nse_historical_bhav.py --start 2020-01-01 --end 2023-12-31 "
            "--out /data/bhav\n"
        )
    )
    parser.add_argument(
        "--start", default=START_DATE,
        help=f"Start date YYYY-MM-DD  (default: {START_DATE})"
    )
    parser.add_argument(
        "--end", default=END_DATE,
        help=f"End date   YYYY-MM-DD  (default: {END_DATE})"
    )
    parser.add_argument(
        "--out", default=OUTPUT_ROOT,
        help=f"Output root folder     (default: {OUTPUT_ROOT})"
    )
    args = parser.parse_args()

    try:
        start = date.fromisoformat(args.start)
        end   = date.fromisoformat(args.end)
    except ValueError as exc:
        parser.error(f"Invalid date format: {exc}  (expected YYYY-MM-DD)")

    if start > end:
        parser.error(f"--start ({start}) must be on or before --end ({end})")

    return start, end, args.out


# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    start_date, end_date, out_root = parse_args()
    run(start_date, end_date, out_root)
