"""
Shared Playwright-based page fetcher, used by match_jobs.py and monitor_careers.py.
Fetches a URL with a headless browser (handles JS-rendered pages) and returns
the visible text content.
"""

import re

from playwright.sync_api import sync_playwright


LOAD_MORE_PATTERN = re.compile(r"load more|show more|view more|see more", re.IGNORECASE)



def fetch_page_links(url: str, timeout: int = 30000, max_expand_attempts: int = 5) -> list[str]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            # Best-effort: click "load more"-style buttons a few times
            for _ in range(max_expand_attempts):
                clicked = False
                for el in page.locator("button, a").all():
                    try:
                        text = el.inner_text(timeout=500)
                    except Exception:
                        continue
                    if text and LOAD_MORE_PATTERN.search(text):
                        try:
                            el.click(timeout=5000)
                            page.wait_for_timeout(5000)
                            clicked = True
                        except Exception:
                            pass
                        break
                if not clicked:
                    break

            # Best-effort: a few scroll passes for infinite-scroll pages
            for _ in range(3):
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(1000)

            hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        finally:
            browser.close()
    return hrefs


def fetch_page_text(url: str, timeout: int = 30000) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        try:
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)  # let JS-rendered content populate
            text = page.inner_text("body")
        finally:
            browser.close()
    return text


if __name__ == "__main__":
    test_url = "https://jobs.thetorocompany.com/search-jobs/India/"
    print(fetch_page_text(test_url)[:500])