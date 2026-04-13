try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT = True
except ImportError:
    _PLAYWRIGHT = False


class WebAgent:
    def __init__(self):
        if not _PLAYWRIGHT:
            raise RuntimeError(
                "playwright is not installed. "
                "Run: pip install playwright && playwright install chromium"
            )
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.page = self.browser.new_page()

    def goto(self, url):
        self.page.goto(url)

    def login(self, email, password):
        self.page.fill("input[type=email]", email)
        self.page.fill("input[type=password]", password)
        self.page.click("button[type=submit]")

    def click(self, selector):
        self.page.click(selector)

    def close(self):
        self.browser.close()
        self.pw.stop()
