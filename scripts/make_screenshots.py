"""Regenerate the README screenshots.

Drives the running app with Playwright so every image is reproducible: when
prices change, re-run this rather than re-cropping by hand.

    streamlit run app.py --server.port 8502 --theme.base light
    python scripts/make_screenshots.py

Uses the system Chrome (channel="chrome") so it needs no browser download.
"""

import pathlib
import sys

from playwright.sync_api import sync_playwright

URL = "http://localhost:8502"
OUT = pathlib.Path(__file__).resolve().parent.parent / "docs"
VIEWPORT = {"width": 1440, "height": 900}
SETTLE_MS = 1800  # Streamlit reruns on every widget change; let it land.


def select(page, index: int, option: str) -> None:
    """Pick an option from the nth st.selectbox.

    Click the inner [role="combobox"], not the stSelectbox wrapper -- the
    wrapper includes the label, and a click landing there does not open the
    dropdown.
    """
    box = page.locator('[data-testid="stSelectbox"]').nth(index)
    box.scroll_into_view_if_needed()
    box.locator('[role="combobox"]').click()
    page.wait_for_timeout(600)
    page.locator('[role="option"]', has_text=option).first.click()
    page.wait_for_timeout(SETTLE_MS)


def check(page, index: int, on: bool) -> None:
    """Set the nth st.checkbox to `on`."""
    box = page.locator('[data-testid="stCheckbox"]').nth(index).locator("input")
    if box.is_checked() != on:
        page.locator('[data-testid="stCheckbox"]').nth(index).click()
        page.wait_for_timeout(SETTLE_MS)


def capture_section(page, path, top_text: str, bottom_selector: str, pad: int = 20) -> None:
    """Screenshot from a heading down to the bottom of a later element.

    Scrolls the heading to the top of the viewport first, so that everything
    below it is on screen and the clip can stay viewport-relative. Uses the
    main content column's own x/width rather than the heading's -- a heading is
    only as wide as its text, which crops the section in half.
    """
    top = page.locator(f"text={top_text}").first
    top.evaluate("el => el.scrollIntoView({block: 'start'})")
    page.wait_for_timeout(1200)

    top_box = top.bounding_box()
    bottom_box = page.locator(bottom_selector).last.bounding_box()
    column = page.locator('[data-testid="stMainBlockContainer"]').first.bounding_box()
    if not (top_box and bottom_box and column):
        raise RuntimeError(f"could not measure section {top_text!r}")

    page.screenshot(path=path, clip={
        "x": max(bottom_box["x"] - pad, 0),
        "y": max(top_box["y"] - pad, 0),
        "width": min(bottom_box["width"] + pad * 2, column["width"]),
        "height": bottom_box["y"] + bottom_box["height"] - top_box["y"] + pad * 2,
    })


def main() -> int:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
        page.goto(URL, wait_until="networkidle")
        page.wait_for_selector("h1", timeout=30_000)

        # Hide Streamlit's dev chrome (the Deploy button and hamburger). It is
        # not part of the product, and it makes README images look unfinished.
        page.add_style_tag(content="""
            [data-testid="stToolbar"], [data-testid="stDecoration"],
            [data-testid="stStatusWidget"], header { display: none !important; }
        """)
        page.wait_for_timeout(3000)

        # 1. Overview -- the default customer-support scenario.
        page.screenshot(path=OUT / "01-overview.png")
        print("01-overview.png")

        # 2. The caching trap: a low-volume pilot where caching costs money.
        select(page, 0, "Low-volume pilot")
        # Exact label, not a prefix: `has_text` is a substring match, so
        # "Claude Opus 5" would also match "Claude Opus 5.5" and silently pick
        # whichever renders first.
        select(page, 1, "Claude Opus 5.5")
        capture_section(
            page, OUT / "02-caching-trap.png",
            "Is prompt caching worth it here?", '[data-testid="stAlert"]',
        )
        print("02-caching-trap.png")

        # 3. The sensitivity curve, with the breakeven line marked.
        chart = page.locator('[data-testid="stVegaLiteChart"]').last
        chart.scroll_into_view_if_needed()
        page.wait_for_timeout(1200)
        chart.screenshot(path=OUT / "03-breakeven-curve.png")
        print("03-breakeven-curve.png")

        # 4. Batch + caching: allowed by the price sheet, unrealistic in practice.
        select(page, 0, "Customer support agent")
        check(page, 1, True)   # Batch API on, caching already on at 90%
        page.wait_for_timeout(1200)
        alert = page.locator('[data-testid="stAlert"]').first
        alert.scroll_into_view_if_needed()
        page.wait_for_timeout(800)
        alert.screenshot(path=OUT / "04-batch-warning.png")
        print("04-batch-warning.png")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
