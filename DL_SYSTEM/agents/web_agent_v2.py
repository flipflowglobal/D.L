try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    _PLAYWRIGHT = True
except ImportError:
    _PLAYWRIGHT = False

import random
import time


class WebAgent:
    def __init__(self, headless=True):
        if not _PLAYWRIGHT:
            raise RuntimeError(
                "playwright is not installed. "
                "Run: pip install playwright && playwright install chromium\n"
                "Not supported on Android/Termux."
            )
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=headless)
        self.context = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800}
        )
        self.page = self.context.new_page()

    def human_delay(self, a=0.5, b=1.5):
        time.sleep(random.uniform(a, b))

    def goto(self, url, timeout=30000):
        self.page.goto(url, timeout=timeout)
        self.page.wait_for_load_state("networkidle")

    def safe_fill(self, selector, value):
        self.page.wait_for_selector(selector, timeout=15000)
        self.page.fill(selector, value)
        self.human_delay()

    def safe_click(self, selector):
        self.page.wait_for_selector(selector, timeout=15000)
        self.page.click(selector)
        self.human_delay()

    def login_generic(self, email, password):
        try:
            self.safe_fill("input[type=email], input[name*=email i]", email)
            self.safe_fill("input[type=password]", password)
            self.safe_click("button[type=submit], button:has-text('Login'), button:has-text('Sign in')")
            self.page.wait_for_load_state("networkidle")
        except PWTimeout:
            raise RuntimeError("Login selectors failed; refine selectors.")

    def close(self):
        self.context.close()
        self.browser.close()
        self.pw.stop()
