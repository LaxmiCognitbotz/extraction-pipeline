"""
RECPDCL / RECTPCL Tender Scraper
Target: https://www.recpdcl.in/rectpcltender

NOTE: This file is vendored into `nct_extraction` so the extraction pipeline can
invoke it as an internal tool when needed. Keep scraper logic unchanged.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlparse

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("Run: pip install playwright && python -m playwright install chromium")

try:
    import requests
except ImportError:
    sys.exit("Run: pip install requests")

from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://www.recpdcl.in"
TENDER_URL = "https://www.recpdcl.in/rectpcltender"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ==== Proxy Settings ====
PROXY_ENABLED = os.getenv("PROXY_ENABLED", "false").lower() == "true"
PROXY_URL = os.getenv("PROXY_URL", "")
PROXY_INSECURE_SSL = os.getenv("PROXY_INSECURE_SSL", "false").lower() == "true"


# ==== Proxy Helpers ====
def get_proxies() -> dict | None:
    return {"http": PROXY_URL, "https": PROXY_URL} if PROXY_ENABLED else None


def get_verify() -> bool:
    return not PROXY_INSECURE_SSL


# Matched against child-link TEXT only (not the URL).
# Boundary rule: not preceded by a letter, not followed by a letter.
KEYWORDS = [
    "Corrigendum",
    "Extension",
    "Successful",
    "RFP",
    "Postponement",
    "Qualified",
    "Amendment",
]


def keyword_in_text(link_text: str) -> str | None:
    """Return the first matched keyword if link_text contains it, else None."""
    for kw in KEYWORDS:
        pat = rf"(?<![A-Za-z]){re.escape(kw)}(?![A-Za-z])"
        if re.search(pat, link_text, re.IGNORECASE):
            return kw
    return None


def title_matches(title: str, user_input: str) -> bool:
    """Exact substring match (case-insensitive)."""
    return user_input.strip().lower() in title.strip().lower()


# ==== Child-link extraction ====
def extract_child_links(container) -> list[dict]:
    """
    Pull every PDF child link from a tender container element.
    """
    links: list[dict] = []
    seen: set[str] = set()

    def add(text: str, url: str):
        url = url.strip()
        if not url or url in seen:
            return
        is_pdf = ".pdf" in url.lower() or re.search(r"\(pdf\s*file\)", text, re.IGNORECASE)
        if is_pdf:
            seen.add(url)
            links.append({"text": text.strip(), "url": url})

    # ── All <a href> inside the container ──
    for a in container.query_selector_all("a[href]"):
        href = (a.get_attribute("href") or "").strip()
        text = a.inner_text().strip()
        if href:
            full = href if href.startswith("http") else urljoin(BASE_URL, href)
            add(text, full)

    raw = container.inner_html()

    # ── onclick patterns ──
    for m in re.finditer(
        r"""(?:window\.open|location\.href\s*=|open\()\s*['"](.*?\.pdf[^'"]*?)['"]""",
        raw,
        re.IGNORECASE,
    ):
        url = m.group(1).strip()
        full = url if url.startswith("http") else urljoin(BASE_URL, url)
        add(os.path.basename(urlparse(full).path), full)

    # ── data-href / data-src / data-url ──
    for m in re.finditer(
        r"""data-(?:href|src|url)\s*=\s*['"](.*?\.pdf[^'"]*?)['"]""",
        raw,
        re.IGNORECASE,
    ):
        url = m.group(1).strip()
        full = url if url.startswith("http") else urljoin(BASE_URL, url)
        add(os.path.basename(urlparse(full).path), full)

    return links


# Title extraction helper
def _extract_title(container) -> str:
    """
    Try several strategies to find the tender title inside a container.
    """
    bold = container.query_selector("b, strong")
    if bold:
        t = bold.inner_text().strip()
        if t:
            return t

    # Fallback: first non-blank non-pdf line
    for line in container.inner_text().split("\n"):
        clean = line.strip().lstrip("○•·–-► ").strip()
        if clean and not re.search(r"\(pdf\s*file\)", clean, re.I):
            return clean

    return ""


# Page scanner
def scan_page(page, user_input: str) -> list[dict]:
    results = []
    serial = 0

    # ── Strategy A: table rows ──
    rows = page.query_selector_all("table tr")
    if rows:
        for tr in rows:
            cells = tr.query_selector_all("td")
            if not cells:
                continue
            # Title usually in the first <td> that has a bold/strong
            title = ""
            for td in cells:
                t = _extract_title(td)
                if t:
                    title = t
                    break
            if not title:
                continue

            if not title_matches(title, user_input):
                continue

            serial += 1
            links = extract_child_links(tr)
            results.append({"sr_no": serial, "title": title.strip(), "all_links": links})

    if results:
        return results

    # ── Strategy B: div-based containers ──
    containers = page.query_selector_all("div, li")
    for c in containers:
        title = _extract_title(c)
        if not title:
            continue
        if not title_matches(title, user_input):
            continue
        serial += 1
        links = extract_child_links(c)
        results.append({"sr_no": serial, "title": title.strip(), "all_links": links})

    return results


def make_folder_name(user_input: str, max_len: int = 60) -> str:
    """
    Generate a short readable folder name from user input.
    """
    text = user_input.strip()
    folder = unicodedata.normalize("NFKD", text)
    folder = re.sub(r"[^\w\s\-]", "_", folder)
    folder = re.sub(r"\s+", "_", folder.strip())
    folder = re.sub(r"_+", "_", folder).strip("_")
    return folder[:max_len]


def goto_retry(page, url: str, wait_ms: int = 3000, retries: int = 3):
    # These errors mean the site is blocked/unreachable — no point retrying
    HARD_NETWORK_ERRORS = (
        "ERR_CONNECTION_RESET",
        "ERR_CONNECTION_TIMED_OUT",
        "ERR_CONNECTION_REFUSED",
        "ERR_NAME_NOT_RESOLVED",
        "net::ERR_",
    )
    for attempt in range(1, retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(wait_ms)
            return
        except PWTimeout:
            print(f"  [warn] timeout {attempt}/{retries}")
            if attempt == retries:
                raise
            time.sleep(attempt * 5)
        except Exception as e:
            err_str = str(e)
            # Hard network error — raise immediately, no retry
            if any(marker in err_str for marker in HARD_NETWORK_ERRORS):
                print(f"  [!] Hard network error — site unreachable, aborting retries.")
                raise
            print(f"  [warn] error {attempt}/{retries}: {err_str[:120]}")
            if attempt == retries:
                raise
            time.sleep(5)



def paginate_all(page):
    # Current RECPDCL page design shows all tenders without pagination controls.
    return


def download_pdf(url: str, dest: Path, session: requests.Session) -> bool:
    try:
        r = session.get(url, timeout=60, verify=get_verify(), proxies=get_proxies())
        r.raise_for_status()
        dest.write_bytes(r.content)
        print(f"         ✓  Downloaded -> {dest.name}")
        return True
    except Exception as e:
        print(f"         ✗  Download failed: {e}")
        return False


def run(user_input: str, output_dir: Path):
    proxy = get_proxies()
    pw_proxy = {"server": PROXY_URL} if PROXY_ENABLED and PROXY_URL else None

    session = requests.Session()
    if proxy:
        session.proxies.update(proxy)
    session.verify = get_verify()
    session.headers.update(
        {
            "User-Agent": UA,
            "Referer": BASE_URL + "/",
            "Accept": "application/pdf,*/*",
        }
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--ignore-certificate-errors"],
            proxy=pw_proxy,
        )
        ctx = browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 900},
            ignore_https_errors=True,
            proxy=pw_proxy,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = ctx.new_page()

        print(f"[1] Loading: {TENDER_URL}")
        goto_retry(page, TENDER_URL, wait_ms=4000)
        paginate_all(page)

        print(f'[2] Scanning for: "{user_input}"\n')
        matched_entries = scan_page(page, user_input)

        if not matched_entries:
            print("[!] No entry found whose title contains that input.")
            print("    Tip: try a shorter substring of the exact title text.")
            browser.close()
            return

        total_downloaded = 0
        folder_name = make_folder_name(user_input)
        save_dir = output_dir / folder_name

        for entry in matched_entries:
            # Filter child links by keyword
            to_download = [lnk for lnk in entry["all_links"] if keyword_in_text(lnk["text"])]

            if not to_download:
                print(f"  [Tender #{entry['sr_no']}] No links matched any keyword.\n")
                continue

            print(f"Downloading {len(to_download)} matched PDF(s) …\n")

            save_dir.mkdir(parents=True, exist_ok=True)

            # Build existing-file index (strip leading counter prefix)
            existing: dict[str, str] = {}
            for f in os.listdir(save_dir):
                if "_" in f and f.split("_", 1)[0].isdigit():
                    original = f.split("_", 1)[1]
                    existing[original] = f

            # Deduplicate filenames in new URL list
            ordered_names: list[str] = []
            for pdf in to_download:
                raw = os.path.basename(urlparse(pdf["url"]).path)
                if not raw.lower().endswith(".pdf"):
                    raw += ".pdf"
                ordered_names.append(raw)

            seen_counts: dict[str, int] = {}
            for i, name in enumerate(ordered_names):
                seen_counts[name] = seen_counts.get(name, 0) + 1
                if seen_counts[name] > 1:
                    base, ext = (name.rsplit(".", 1) if "." in name else (name, ""))
                    suffix = f".{ext}" if ext else ""
                    ordered_names[i] = f"{base}-{seen_counts[name]}{suffix}"

            for serial, (pdf, raw_fname) in enumerate(zip(to_download, ordered_names), start=1):
                new_fname = f"{serial:02d}_{raw_fname}"
                dest = save_dir / new_fname

                print(f"  [{serial:02d}]  {pdf['text']}")
                print(f"        {pdf['url']}")

                if raw_fname in existing:
                    old_path = save_dir / existing[raw_fname]
                    if old_path != dest:
                        os.replace(old_path, dest)
                        print(f"         ✓  Renamed {existing[raw_fname]} → {new_fname}")
                    else:
                        print(f"         ✓  Already exists (Skipped)")
                else:
                    ok = download_pdf(pdf["url"], dest, session)
                    if ok:
                        total_downloaded += 1
                    time.sleep(0.4)

        browser.close()

    print(f"\nDone.  {total_downloaded} PDF(s) saved to:")
    print(f"  {save_dir.resolve()}\n")


# ==== CLI ====
def main():
    global TENDER_URL
    ap = argparse.ArgumentParser(
        description="RECPDCL / RECTPCL Tender Scraper — exact match + keyword filter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--query", "-q", default=None, help="Substring of the tender title to match (exact, case-insensitive)")
    ap.add_argument("--output", "-o", default="./uploads/RECPDCL-RECTPCL-TENDER", help="Master output directory  (default: ./uploads/RECPDCL-RECTPCL-TENDER)")
    ap.add_argument("--url", "-u", default=TENDER_URL, help=f"Tender page URL  (default: {TENDER_URL})")
    args = ap.parse_args()

    # Allow overriding the target URL at runtime (handy for archive page, etc.)
    TENDER_URL = args.url

    user_input = args.query
    if not user_input:
        print("─" * 60)
        print("RECPDCL / RECTPCL Tender Scraper")
        print("─" * 60)
        user_input = input("Input > ").strip()
        if not user_input:
            sys.exit("No input given.")

    Path(args.output).mkdir(parents=True, exist_ok=True)
    run(user_input, Path(args.output))


if __name__ == "__main__":
    main()

