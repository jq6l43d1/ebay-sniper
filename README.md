# ebay-sniper

A last-second bidding tool for ebay.com, built with Python and [Playwright](https://playwright.dev/python/).

It drives a real Chromium browser with your saved eBay login and times the final click on the bid button so it lands a fraction of a second before the auction closes. You can run it anywhere from "I click and type, the script only times the submit" up to fully scheduled and hands-off.

> **Disclaimer.** I built this as a research project into browser automation and timing. eBay's User Agreement restricts automated access to the site, so using this on a real account may violate their terms and could get the account restricted. Use it at your own risk. Always try `--dry-run` first, because once a bid is submitted it can't be taken back.

## Why snipe

On eBay the highest maximum bid wins, but bidding early shows your interest and invites bidding wars. Placing your real maximum in the last second or two leaves other bidders no time to react. This script handles that timing so I don't have to sit and watch the clock.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## One-time login

```bash
python snipe.py --setup
```

Sign in with your eBay email and password in the browser that opens, then press Enter in the terminal. The session is saved to `./user-data/`, which is gitignored. Google sign-in is blocked in automated browsers, so if your eBay account is linked to Google, set an eBay password in your account settings first.

## Modes

Pick how much you want automated by adding flags.

**Manual.** You click "Place bid" and type your bid. The script only times the submit.

```bash
python snipe.py <url>
```

**Auto-type the bid.** You click "Place bid" and the script types the amount.

```bash
python snipe.py <url> --max-bid 145
```

**Hands-off.** The script clicks "Place bid" with real mouse movement, types the amount and submits. If the click doesn't open the bid window, it waits for you to click instead.

```bash
python snipe.py <url> --max-bid 145 --auto-open
```

**Scheduled.** The script sleeps until about 90 seconds before the end, then runs the hands-off flow. You can start it days ahead.

```bash
python snipe.py <url> --max-bid 145 --auto-open --schedule
```

## Utilities

```bash
python snipe.py <url> --inspect            # print the listing's exact end time
python snipe.py --calibrate                # measure latency and suggest --lead-ms
python snipe.py <url> --max-bid 145 --dry-run   # run everything except the final click
python snipe.py <url> --dry-run --end-in 2m     # rehearse against a fake end time
```

## Flags

| Flag | What it does |
|---|---|
| `--end-time` | Override the detected end time (`HH:MM:SS` local, or ISO) |
| `--end-in` | End time relative to now, e.g. `2m` or `90s`. Useful with `--dry-run` |
| `--max-bid` | Amount to type into the bid window |
| `--lead-ms` | Click this many milliseconds before the end (default 1500) |
| `--auto-lead` | Measure latency at startup and set `--lead-ms` from it |
| `--schedule` | Sleep until 90 seconds before the end before opening the browser |
| `--auto-open` | Click "Place bid" automatically |
| `--dry-run` | Do everything except submit the bid, then save a screenshot |
| `--profile-dir` | Browser profile to use. Give each parallel snipe its own copy |
| `--setup` | Open a browser to log in and save the session |
| `--inspect` | Print the end time and exit |
| `--calibrate` | Suggest `--lead-ms` from latency samples |

## How it works

**Finding the end time.** The listing page embeds the end time in its JSON. The script first fetches the page as a plain HTTP request using the saved login cookies. That's fast and gets past eBay's bot protection, which blocks anonymous headless fetches. If that fails it renders the page in the browser instead.

**Timing the click.** The script sleeps until half a second before the target, then busy-waits in half-millisecond steps so the click fires within a few milliseconds of the target. `--lead-ms` covers network round-trip time. `--calibrate` takes several samples and suggests the 95th-percentile latency plus 300 ms of margin.

**Finding the submit button.** The button's label depends on state: "Bid" for a first bid, and "Increase bid" or "Increase max bid" if you're already bidding. `find_modal_submit_button()` tries several lookups (accessible role, exact text, substring) and takes the first visible match. The pointer hovers over the button ahead of time so the final click is a single event.

**Looking like a normal browser.** Playwright's automation flags are switched off, and the "Place bid" click is a real mouse move and click at the button's coordinates rather than a synthetic DOM event.

**When something goes wrong,** the script saves a screenshot and the page HTML to `debug/`. It also prints every visible input and button so you can see what changed on eBay's side.

## Caveats

- Your system clock has to be accurate. Keep NTP running.
- eBay's bid window has no separate confirmation step. Once the script clicks, the bid is placed.
- Only ebay.com is supported. Other regional sites use different page layouts.
- eBay changes its pages from time to time. If a selector breaks, update the constants at the top of `snipe.py`.

## License

MIT
