#!/usr/bin/env python3
"""eBay sniper for ebay.com. Handles the timing of the final bid click.

By default YOU click 'Place bid' to open the modal; the script can auto-fill
the bid amount and times the submit click precisely. With --auto-open and
--max-bid it's fully hands-off after launch.

Examples:
    python snipe.py --setup                              # one-time login
    python snipe.py URL --inspect                        # find end time
    python snipe.py --calibrate                          # tune --lead-ms

    # Manual (you click + type), script just times the submit:
    python snipe.py URL

    # You click 'Place bid'; script types the bid and submits:
    python snipe.py URL --max-bid 145

    # Fully hands-off (script clicks 'Place bid', types, submits):
    python snipe.py URL --max-bid 145 --auto-open

    # Fully hands-off AND scheduled (script sleeps until 90s before end):
    python snipe.py URL --max-bid 145 --auto-open --schedule
"""

import argparse
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_PROFILE_DIR = Path(__file__).parent / "user-data"
# Set by main() before launch() is called. Allows parallel snipes against
# different profile dirs (each Chromium process needs exclusive access).
PROFILE_DIR = DEFAULT_PROFILE_DIR

# Listing-page "Place bid" button. Opens the bid modal.
LISTING_BID_BTN = '#bidBtn_btn'
# Bid-amount input inside the modal. ID pattern is stable across listings.
BID_INPUT_SEL = 'input[id*="@placebid"]:visible'
# Modal submit button. Clicking this places the bid in one shot. eBay's
# 2025 flow has no separate "Confirm bid" step. Button text varies by state
# ("Bid" for first-time bidders, "Increase bid" / "Increase max bid" when
# already bidding), so find_modal_submit_button() tries several strategies.
MODAL_BID_BTN_NAMES = ["Bid", "Increase bid", "Increase max bid"]


def find_modal_submit_button(page):
    """Return (locator, strategy_name) for the modal's submit button,
    or (None, None) if nothing matches yet."""
    strategies = []
    for name in MODAL_BID_BTN_NAMES:
        strategies.append(
            (f"role:button name={name!r}",
             page.get_by_role("button", name=name, exact=True))
        )
        strategies.append(
            (f"text-is({name!r}) submit",
             page.locator(f'button[type="submit"]:text-is("{name}")'))
        )
    strategies.append(
        ("filter Increase…bid",
         page.locator('button[type="submit"]')
             .filter(has_text="Increase").filter(has_text="bid"))
    )
    for sname, loc in strategies:
        try:
            if loc.count() >= 1 and loc.first.is_visible(timeout=200):
                return loc.first, sname
        except Exception:
            continue
    return None, None


def parse_end_time(s: str) -> datetime:
    """Parse 'HH:MM:SS' (rolls to tomorrow if past) or ISO 'YYYY-MM-DDTHH:MM:SS'."""
    s = s.strip()
    if "T" in s or ("-" in s and " " in s):
        return datetime.fromisoformat(s.replace(" ", "T"))
    t = datetime.strptime(s, "%H:%M:%S").time()
    today = date.today()
    candidate = datetime.combine(today, t)
    if candidate <= datetime.now():
        candidate = datetime.combine(today + timedelta(days=1), t)
    return candidate


def launch(pw, headless: bool):
    """Launch with the persistent eBay-login profile. Use for the actual
    snipe; do NOT use for headless inspection tasks. Loading the heavy
    persistent profile in headless mode has been seen to crash the
    renderer on my machine."""
    PROFILE_DIR.mkdir(exist_ok=True)
    # Chromium's per-profile singleton markers. If the profile dir was
    # copied (e.g. for parallel snipes), they point at the original PID
    # and make a new launch attach to / be killed by that session. Always
    # clear them. If another Chromium really owns this dir, it'll still
    # error out on its own.
    for marker in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        (PROFILE_DIR / marker).unlink(missing_ok=True)
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
        user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    )
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return ctx


def launch_ephemeral(pw, headless: bool = True):
    """Fresh non-persistent context for inspection / calibration tasks
    that don't need the eBay login. Lighter and more stable than launch()
    since no profile is loaded. Returns (browser, ctx); caller must
    close ctx then browser."""
    browser = pw.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    )
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return browser, ctx


def dump_debug(page, label: str):
    """Save HTML + screenshot + list visible inputs/buttons for diagnosis."""
    debug_dir = Path(__file__).parent / "debug"
    debug_dir.mkdir(exist_ok=True)
    try:
        page.screenshot(path=str(debug_dir / f"{label}.png"), full_page=True)
    except Exception as e:
        print(f"(screenshot failed: {e})")
    try:
        (debug_dir / f"{label}.html").write_text(page.content())
    except Exception as e:
        print(f"(html dump failed: {e})")
    print(f"\n--- DEBUG DUMP [{label}] ---")
    print(f"Current URL: {page.url}")
    print(f"Saved: debug/{label}.png and debug/{label}.html")
    print("\nVisible <input> elements:")
    try:
        for inp in page.locator("input:visible").all()[:30]:
            print(f"  {inp.evaluate('e => ({id: e.id, name: e.name, type: e.type, placeholder: e.placeholder, aria: e.getAttribute(\"aria-label\")})')}")
    except Exception as e:
        print(f"  (failed: {e})")
    print("\nVisible <button> elements:")
    try:
        for btn in page.locator("button:visible").all()[:30]:
            txt = (btn.text_content() or "").strip()[:60]
            attrs = btn.evaluate('e => ({id: e.id, type: e.type, aria: e.getAttribute("aria-label")})')
            print(f"  text={txt!r} {attrs}")
    except Exception as e:
        print(f"  (failed: {e})")
    print("--- END DEBUG DUMP ---\n")


def setup():
    from playwright.sync_api import sync_playwright
    print(f"Opening browser with profile {PROFILE_DIR}")
    with sync_playwright() as pw:
        ctx = launch(pw, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.ebay.com/signin/",
                  wait_until="domcontentloaded", timeout=60_000)
        print("Log into eBay in the launched browser.")
        input("Press Enter here when done. The browser will close. ")
        ctx.close()


def extract_end_time(html: str) -> datetime | None:
    """Look for an embedded end-time in the listing HTML."""
    patterns = [
        r'"endTime"\s*:\s*"([^"]+)"',
        r'"itemEndDate"\s*:\s*"([^"]+)"',
        r'"endDate"\s*:\s*"([^"]+)"',
        r'data-end-date="([^"]+)"',
        r'data-endtime="([^"]+)"',
        r'<meta[^>]+endTime[^>]+content="([^"]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if not m:
            continue
        raw = m.group(1)
        try:
            if raw.isdigit():
                ts = int(raw)
                if ts > 10**12:
                    ts /= 1000
                return datetime.fromtimestamp(ts).astimezone().replace(tzinfo=None)
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo:
                dt = dt.astimezone().replace(tzinfo=None)
            return dt
        except ValueError:
            continue
    return None


def _save_html_for_debug(html: str, url: str, title: str = "") -> Path:
    debug_dir = Path(__file__).parent / "debug"
    debug_dir.mkdir(exist_ok=True)
    debug_file = debug_dir / "fetch_end_time_no_match.html"
    debug_file.write_text(html)
    print(f"    HTML size:  {len(html):,} chars")
    if title:
        print(f"    page title: {title!r}")
    print(f"    page URL:   {url}")
    print(f"    saved to:   {debug_file}")
    return debug_file


def _fetch_end_time_via_session(url: str) -> datetime | None:
    """HTTP fetch using the PERSISTENT profile's cookies. eBay's WAF lets
    authenticated sessions through, and since no page is rendered, the
    headless renderer can't crash."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = launch(pw, headless=True)
        try:
            resp = ctx.request.get(url, timeout=30_000)
            html = resp.text()
            result = extract_end_time(html)
            if result is None:
                _save_html_for_debug(html, url)
            return result
        finally:
            ctx.close()


def _fetch_end_time_render(url: str) -> datetime | None:
    """Last-resort: actually render the page with the persistent profile.
    Has been seen to crash the headless renderer on some environments."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = launch(pw, headless=True)
        try:
            ctx.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ("image", "media", "font")
                else route.continue_()
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_000)
            html = page.content()
            result = extract_end_time(html)
            if result is None:
                title = ""
                try:
                    title = page.title()
                except Exception:
                    pass
                _save_html_for_debug(html, page.url, title)
            return result
        finally:
            ctx.close()


def fetch_end_time(url: str, retries: int = 2) -> datetime | None:
    """Primary: authenticated HTTP via the persistent profile's cookies.
    Fallback: render the listing in the persistent profile.
    Requires a prior --setup so the profile has eBay cookies."""
    print("  Trying authenticated HTTP fetch ...")
    for attempt in range(retries + 1):
        try:
            result = _fetch_end_time_via_session(url)
            if result:
                return result
            print(f"  HTTP fetch returned no end-time (attempt {attempt + 1}).")
        except Exception as e:
            print(f"  HTTP fetch failed (attempt {attempt + 1}): "
                  f"{type(e).__name__}: {e}")
        if attempt < retries:
            time.sleep(2)

    print("  Falling back to rendered fetch ...")
    for attempt in range(retries + 1):
        try:
            result = _fetch_end_time_render(url)
            if result:
                return result
            print(f"  Render fetch returned no end-time (attempt {attempt + 1}).")
        except Exception as e:
            print(f"  Render fetch failed (attempt {attempt + 1}): "
                  f"{type(e).__name__}: {e}")
        if attempt < retries:
            time.sleep(3)
    return None


def inspect(url: str):
    end = fetch_end_time(url)
    if end:
        print(f"End time (parsed): {end.isoformat(timespec='seconds')}")
        print(f"  → --end-time {end.strftime('%H:%M:%S')}")
    else:
        print("End time: NOT FOUND in page HTML.")


def calibrate(count: int) -> int | None:
    """Measure round-trip latency to eBay. Returns the suggested --lead-ms
    (p95 + 300ms margin), or None if no samples could be collected."""
    from playwright.sync_api import sync_playwright
    print(f"Pinging www.ebay.com {count} times via Playwright HTTP context ...")
    samples = []
    with sync_playwright() as pw:
        browser, ctx = launch_ephemeral(pw, headless=True)
        try:
            for i in range(count):
                t0 = time.perf_counter()
                try:
                    resp = ctx.request.get("https://www.ebay.com/",
                                           timeout=10_000)
                    _ = resp.body()
                    elapsed = (time.perf_counter() - t0) * 1000
                    samples.append(elapsed)
                    print(f"  ping {i+1}: {elapsed:.0f} ms")
                except Exception as e:
                    print(f"  ping {i+1}: error {e}")
                time.sleep(0.3)
        finally:
            ctx.close()
            browser.close()
    if not samples:
        print("No samples collected; calibration failed.")
        return None
    samples.sort()
    median = samples[len(samples) // 2]
    p95 = samples[max(0, int(len(samples) * 0.95) - 1)]
    suggest = max(500, int(p95 + 300))
    print(f"Latency: median {median:.0f} ms, p95 {p95:.0f} ms → "
          f"--lead-ms {suggest}")
    return suggest


def parse_duration(s: str) -> float:
    """Parse a duration like '2m', '30s', '1h', or a plain number (seconds)."""
    s = s.strip().lower()
    units = {"h": 3600, "m": 60, "s": 1}
    if s and s[-1] in units:
        return float(s[:-1]) * units[s[-1]]
    return float(s)


def sleep_until(target: datetime, label: str):
    """Sleep until target, with progress updates roughly every 5 minutes."""
    wait_total = (target - datetime.now()).total_seconds()
    if wait_total <= 0:
        return
    print(f"{label}: sleeping until {target.isoformat(timespec='seconds')} "
          f"(~{wait_total/60:.1f} min)")
    while True:
        remaining = (target - datetime.now()).total_seconds()
        if remaining <= 0:
            return
        if remaining > 60:
            print(f"  ... {remaining/60:.1f} min remaining")
            time.sleep(min(remaining - 30, 300))
        else:
            time.sleep(remaining)


def attempt_auto_open_modal(page, timeout_ms: int = 8_000) -> bool:
    """Click 'Place bid' on the listing using real mouse coordinates, which
    generates a trusted user-gesture event that has a better chance of
    evading anti-bot than a synthetic .click(). Returns True if the modal's
    bid input becomes visible, False otherwise."""
    btn = page.locator(LISTING_BID_BTN).first
    try:
        btn.wait_for(state="visible", timeout=5_000)
    except Exception:
        print("  'Place bid' button not found on the listing.")
        return False
    try:
        btn.scroll_into_view_if_needed(timeout=3_000)
    except Exception:
        pass
    box = btn.bounding_box()
    if not box:
        print("  Could not get button bounding box.")
        return False
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    # Multi-step move + small dwell + click. Generates trusted events.
    page.mouse.move(x, y, steps=20)
    time.sleep(0.08)
    page.mouse.click(x, y, delay=60)
    print(f"  Mouse-clicked 'Place bid' at ({x:.0f}, {y:.0f}).")
    try:
        page.locator(BID_INPUT_SEL).first.wait_for(
            state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False


def auto_fill_bid(page, max_bid: float):
    """Fill the bid amount input in the open modal."""
    bid_input = page.locator(BID_INPUT_SEL).first
    bid_input.wait_for(state="visible", timeout=15_000)
    bid_input.click()
    bid_input.press("ControlOrMeta+A")
    bid_input.type(f"{max_bid:.2f}", delay=70)
    print(f"Auto-typed max bid: {max_bid:.2f}")


def snipe(url: str, end_time: datetime | None, max_bid: float | None,
          lead_ms: int, dry_run: bool, schedule: bool, auto_open: bool,
          auto_lead: bool):
    # Resolve end_time. --schedule requires it up-front so we know when to
    # wake. For other modes we can extract it from the live page after
    # navigation (cheaper and dodges WAF/anti-bot on headless fetches).
    if end_time is None and schedule:
        print("Fetching end time from listing (needed for --schedule) ...")
        end_time = fetch_end_time(url)
        if end_time is None:
            raise SystemExit(
                "Could not auto-detect end time, and --schedule needs it "
                "before the sleep. Pass --end-time manually."
            )
        print(f"  → {end_time.isoformat(timespec='seconds')}")

    # Schedule: wait until ~90s before end_time before launching the browser.
    if schedule:
        sleep_until(end_time - timedelta(seconds=90), "Scheduled")

    # Auto-calibrate AFTER schedule sleep (reflects current network).
    if auto_lead:
        print("Auto-calibrating --lead-ms ...")
        calibrated = calibrate(5)
        if calibrated is not None:
            lead_ms = calibrated
            print(f"  → using --lead-ms {lead_ms}")
        else:
            print(f"  → calibration failed; keeping --lead-ms {lead_ms}")

    if dry_run:
        print("*** DRY RUN: will stop before submitting the bid. ***")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = launch(pw, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        # If we still don't have end_time, extract from the live page now.
        # Akamai lets real rendered sessions through, so this works where
        # the standalone headless fetch hits a WAF challenge.
        if end_time is None:
            page.wait_for_timeout(2_000)  # let SSR JSON settle
            try:
                end_time = extract_end_time(page.content())
            except Exception as e:
                print(f"  live extraction failed: {e}")
            if end_time is None:
                raise SystemExit(
                    "Could not extract end time from the live page. "
                    "Pass --end-time manually."
                )
            print(f"End time (extracted): "
                  f"{end_time.isoformat(timespec='seconds')}")

        snipe_at = end_time - timedelta(milliseconds=lead_ms)
        print(f"Auction ends:  {end_time.isoformat(timespec='seconds')}")
        print(f"Will click at: {snipe_at.isoformat(timespec='milliseconds')} "
              f"(lead {lead_ms} ms)")
        if snipe_at <= datetime.now():
            raise SystemExit("Snipe time is already in the past. Aborting.")

        # Step 1: open the modal. Try auto-open, fall back to user click.
        modal_open = False
        if auto_open:
            print("\nAttempting to auto-open the bid modal ...")
            modal_open = attempt_auto_open_modal(page)
            print(f"  → {'success' if modal_open else 'failed; ask user'}")
        if not modal_open:
            print("\nClick 'Place bid' in the browser to open the modal.")
            page.locator(BID_INPUT_SEL).first.wait_for(
                state="visible", timeout=120_000)
            print("Modal detected.")

        # Step 2: fill the bid amount. Auto if --max-bid, else user types.
        if max_bid is not None:
            auto_fill_bid(page, max_bid)
        else:
            print("Type your max bid in 'Your max bid' (script won't fill).")

        # Step 3: detect the submit button, pre-hover, snipe at the right ms.
        print("Waiting for the modal's submit button to become visible ...")
        submit_btn = None
        while True:
            loc, strategy = find_modal_submit_button(page)
            if loc is not None:
                submit_btn = loc
                print(f"Submit button detected via [{strategy}]. Pre-hovering.")
                break
            remaining = (snipe_at - datetime.now()).total_seconds()
            if remaining <= 5:
                dump_debug(page, "submit_btn_not_ready")
                raise SystemExit(
                    f"Snipe time {snipe_at.isoformat()} is {remaining:.1f}s "
                    "away and the modal's submit button is not visible. "
                    "Aborting."
                )
            time.sleep(0.5)
        submit_btn.hover()

        coarse = (snipe_at - datetime.now()).total_seconds() - 0.5
        if coarse > 0:
            print(f"Sleeping {coarse:.1f}s until snipe time ...")
            time.sleep(coarse)
        while datetime.now() < snipe_at:
            time.sleep(0.0005)

        if dry_run:
            print("DRY RUN: reached snipe time, NOT submitting the bid.")
            shot = Path(__file__).parent / "dryrun-at-snipe-time.png"
            page.screenshot(path=str(shot), full_page=True)
            print(f"Screenshot: {shot}")
        else:
            submit_btn.click(no_wait_after=True, force=True)
            clicked_at = datetime.now()
            delta_ms = (clicked_at - snipe_at).total_seconds() * 1000
            print(f"Clicked at {clicked_at.isoformat(timespec='milliseconds')} "
                  f"(delta {delta_ms:+.0f} ms)")
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            shot = Path(__file__).parent / f"snipe-{clicked_at.strftime('%Y%m%d-%H%M%S')}.png"
            page.screenshot(path=str(shot), full_page=True)
            print(f"Screenshot: {shot}")

        input("Press Enter to close the browser. ")
        ctx.close()


def main():
    ap = argparse.ArgumentParser(description="eBay sniper (ebay.com)")
    ap.add_argument("url", nargs="?", help="eBay auction URL")
    ap.add_argument("--end-time",
                    help="Auction end as 'HH:MM:SS' (local) or ISO. "
                         "If omitted, auto-fetched from the listing.")
    ap.add_argument("--end-in",
                    help="Auction end relative to NOW (e.g. '2m', '90s', "
                         "'1h', or plain seconds). Mostly for --dry-run "
                         "testing; mutually exclusive with --end-time.")
    ap.add_argument("--max-bid", type=float,
                    help="If set, script auto-types this into the bid modal. "
                         "Without it, you type the amount yourself.")
    ap.add_argument("--lead-ms", type=int, default=1500,
                    help="Click this many ms before end-time (default 1500). "
                         "Use --calibrate to tune.")
    ap.add_argument("--auto-lead", action="store_true",
                    help="Auto-calibrate --lead-ms at startup (5 pings to "
                         "eBay; overrides --lead-ms with p95 + 300ms).")
    ap.add_argument("--schedule", action="store_true",
                    help="Sleep until ~90s before end-time before launching "
                         "the browser. Set-and-forget mode.")
    ap.add_argument("--auto-open", action="store_true",
                    help="Try to click 'Place bid' on the listing via real "
                         "mouse coordinates. May trip anti-bot; falls back "
                         "to waiting for your manual click.")
    ap.add_argument("--setup", action="store_true",
                    help="Open a browser to log into eBay.")
    ap.add_argument("--inspect", action="store_true",
                    help="Print the listing's end time and exit.")
    ap.add_argument("--calibrate", action="store_true",
                    help="Ping eBay to measure latency; suggest --lead-ms.")
    ap.add_argument("--calibrate-count", type=int, default=10,
                    help="Pings for --calibrate (default 10).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Walk the whole flow but DO NOT submit the bid.")
    ap.add_argument("--profile-dir", type=Path,
                    default=DEFAULT_PROFILE_DIR,
                    help="Chromium profile directory (default: ./user-data). "
                         "For parallel snipes of multiple auctions, give each "
                         "a different dir (cp -r the default to seed login).")
    args = ap.parse_args()

    global PROFILE_DIR
    PROFILE_DIR = args.profile_dir

    if args.setup:
        setup()
        return
    if args.calibrate:
        calibrate(args.calibrate_count)
        return
    if args.inspect:
        if not args.url:
            ap.error("--inspect needs a URL")
        inspect(args.url)
        return

    if not args.url:
        ap.error("url is required (or use --setup / --inspect / --calibrate)")

    if args.end_time and args.end_in:
        ap.error("--end-time and --end-in are mutually exclusive")

    if args.end_in:
        end_time = datetime.now() + timedelta(seconds=parse_duration(args.end_in))
        print(f"--end-in {args.end_in!r} → end_time = "
              f"{end_time.isoformat(timespec='seconds')}")
    elif args.end_time:
        end_time = parse_end_time(args.end_time)
    else:
        end_time = None

    snipe(args.url, end_time, args.max_bid, args.lead_ms,
          args.dry_run, args.schedule, args.auto_open, args.auto_lead)


if __name__ == "__main__":
    main()
