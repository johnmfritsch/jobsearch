"""
Playwright URL resolution microservice.
Runs inside Docker container, listens on :3000.
Accepts POST /resolve {"url": "..."} and returns {"resolved_url": "..."}
after navigating with headless Chromium and waiting for JS redirects to settle.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import sys
import os

TIMEOUT_MS = 35000   # 35 seconds total budget (Adzuna splash says "5 seconds", allow headroom)
POLL_INTERVAL = 0.5  # seconds between URL checks
SETTLE_TIME = 2.0    # seconds to wait after URL changes before considering it final


def _get_domain(url):
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


# Patterns in page text/title that indicate a bot/CAPTCHA challenge page
_BOT_SIGNALS = [
    "prove you are human",
    "verify you are human",
    "are you a robot",
    "captcha",
    "cf-challenge",          # Cloudflare challenge div id
    "challenge-form",        # Cloudflare challenge form
    "ray id",                # Cloudflare Ray ID footer
    "security check",
    "ddos-guard",
    "please enable javascript and cookies",
    "access denied",
    "enable javascript",
]


def _check_for_bot_wall(page, url):
    """
    Check page content for signs of a bot/CAPTCHA challenge.
    Logs a warning if detected. Returns True if bot wall found.
    """
    try:
        title = (page.title() or "").lower()
        # Sample visible text — avoid full innerHTML which can be huge
        body_text = page.evaluate("() => document.body ? document.body.innerText.toLowerCase().slice(0, 2000) : ''")
        combined = title + " " + body_text

        for signal in _BOT_SIGNALS:
            if signal in combined:
                print(f"  [BOT-WALL DETECTED] signal='{signal}' url={url}", flush=True)
                print(f"  [BOT-WALL] page title: '{page.title()}'", flush=True)
                return True
    except Exception as e:
        print(f"  [bot-check error] {e}", flush=True)
    return False


def resolve_url(url):
    """
    Navigate to URL with headless Chromium.
    Strategy: load the page, then poll for the URL to change away from the
    original domain (handles JS window.location redirects that fire after load).
    Returns final URL after redirect settles, or original URL on failure/timeout.
    """
    import time
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            # Apply stealth patches to avoid headless Chromium fingerprint detection
            try:
                from playwright_stealth import stealth_sync
                stealth_sync(page)
                print("  [stealth] applied", flush=True)
            except ImportError:
                print("  [stealth] playwright-stealth not installed, skipping", flush=True)

            start_domain = _get_domain(url)

            # Navigate; use "domcontentloaded" so we don't wait forever on
            # pages that keep firing network requests (like ad trackers)
            page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)

            # Diagnostic: log page title and current URL right after load
            try:
                print(f"  [page-loaded] url={page.url}", flush=True)
                print(f"  [page-loaded] title='{page.title()}'", flush=True)
            except Exception as e:
                print(f"  [page-loaded] error getting title: {e}", flush=True)

            # Check for bot/CAPTCHA wall immediately after page load
            _check_for_bot_wall(page, url)

            # Poll: wait for URL to change away from the starting domain.
            # Use a fixed 30-second poll window (separate from the goto timeout)
            # so Adzuna's "redirecting in 5 seconds" splash has time to fire.
            deadline = time.time() + 30.0
            last_url = page.url
            changed_at = None

            while time.time() < deadline:
                current = page.url
                current_domain = _get_domain(current)

                if current_domain and current_domain != start_domain:
                    # URL has moved off the original domain
                    if changed_at is None:
                        changed_at = time.time()
                        last_url = current
                        print(f"  [redirect detected] {current}", flush=True)
                        # Re-check for bot wall at the new destination
                        _check_for_bot_wall(page, current)
                    elif time.time() - changed_at >= SETTLE_TIME:
                        # Stable for SETTLE_TIME seconds — we're done
                        break
                    else:
                        last_url = current  # keep updating while settling

                time.sleep(POLL_INTERVAL)

            final = page.url
            browser.close()
            return final
    except Exception as e:
        print(f"[resolve_url] error for {url}: {e}", flush=True)
        return url  # on failure, return original unchanged


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/resolve":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                url = body.get("url", "")
                if not url:
                    self._send_json(400, {"error": "missing url"})
                    return
                print(f"[resolve] {url}", flush=True)
                resolved = resolve_url(url)
                print(f"[resolved] {resolved}", flush=True)
                self._send_json(200, {"resolved_url": resolved})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
        else:
            self._send_json(404, {"error": "not found"})

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not found"})

    def _send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Suppress default access log noise; we print our own above
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PLAYWRIGHT_PORT", 3000))
    print(f"Playwright URL resolution service starting on :{port}", flush=True)
    server = HTTPServer(("0.0.0.0", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down.", flush=True)
        sys.exit(0)
