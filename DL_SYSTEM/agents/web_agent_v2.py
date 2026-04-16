"""
DL_SYSTEM/agents/web_agent_v2.py — Playwright-based headless browser agent.

Provides human-like interaction patterns (randomised delays, networkidle
waits) for automated web tasks.  Falls back gracefully if Playwright is
not installed.
"""

from __future__ import annotations

import random
import time

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    _PLAYWRIGHT = True
except ImportError:
    _PLAYWRIGHT = False
    PWTimeout = Exception   # type: ignore[assignment,misc]


class WebAgent:
    """
    Headless browser wrapper with human-delay helpers.

    Raises:
        RuntimeError: if playwright is not installed or not supported
                      on the current platform (e.g. Android/Termux).
    """

    def __init__(self, headless: bool = True) -> None:
        if not _PLAYWRIGHT:
            raise RuntimeError(
                "playwright is not installed. "
                "Run: pip install playwright && playwright install chromium\n"
                "Note: not supported on Android/Termux."
            )
        self.pw      = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=headless)
        self.context = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        self.page = self.context.new_page()

    # ── Interaction helpers ───────────────────────────────────────────────────

    def human_delay(self, low: float = 0.5, high: float = 1.5) -> None:
        """Sleep for a random duration to mimic human interaction speed."""
        time.sleep(random.uniform(low, high))

    def goto(self, url: str, timeout: int = 30_000) -> None:
        """Navigate to *url* and wait for the page to become idle."""
        self.page.goto(url, timeout=timeout)
        self.page.wait_for_load_state("networkidle")

    def safe_fill(self, selector: str, value: str) -> None:
        """Wait for *selector* to appear then fill it with *value*."""
        self.page.wait_for_selector(selector, timeout=15_000)
        self.page.fill(selector, value)
        self.human_delay()

    def safe_click(self, selector: str) -> None:
        """Wait for *selector* to appear then click it."""
        self.page.wait_for_selector(selector, timeout=15_000)
        self.page.click(selector)
        self.human_delay()

    def login_generic(self, email: str, password: str) -> None:
        """
        Attempt a generic email/password login on the current page.

        Raises:
            RuntimeError: login selectors not found within timeout.
        """
        try:
            self.safe_fill("input[type=email], input[name*=email i]", email)
            self.safe_fill("input[type=password]", password)
            self.safe_click(
                "button[type=submit], button:has-text('Login'), button:has-text('Sign in')"
            )
            self.page.wait_for_load_state("networkidle")
        except PWTimeout:
            raise RuntimeError(
                "Login selectors not found within timeout. "
                "Inspect the page and refine selectors in web_agent_v2.py."
            )

    def close(self) -> None:
        """Close the browser and stop Playwright."""
        try:
            self.context.close()
            self.browser.close()
            self.pw.stop()
        except Exception:
            pass
