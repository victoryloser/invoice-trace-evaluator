"""Optional Playwright/Edge check of behavior evidence links and filtering."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.html.resolve().as_uri())
        assert page.locator("#behaviors").is_visible()
        page.locator("#behaviors details summary").first.click()
        link = page.locator("#behaviors a[data-event]").first
        run_index, event_id = link.get_attribute("data-run"), link.get_attribute("data-event")
        link.click()
        assert event_id in page.locator(f"#run{run_index} .detail").inner_text()
        count = page.locator("#case option").count()
        page.locator("#search").fill("no-such-case-for-behavior-test")
        assert page.locator("#case option").count() == 0
        assert page.locator("#behaviors").is_hidden()
        page.locator("#reset").click()
        assert page.locator("#case option").count() == count
        assert page.locator("#behaviors").is_visible()
        page.screenshot(path=str(args.output / "desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(args.output / "mobile.png"), full_page=True)
        browser.close()
    assert not errors, errors
    (args.output / "validation.json").write_text(json.dumps({"passed": True, "page_errors": errors,
        "checks": ["behavior evidence navigation", "empty search hides stale evidence", "reset", "mobile overflow"],
        "input": str(args.html)}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Behavior browser checks passed")


if __name__ == "__main__":
    main()
