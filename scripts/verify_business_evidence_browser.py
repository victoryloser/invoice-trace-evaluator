"""Optional Playwright/Edge checks for invoice provenance and source navigation."""
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
        assert page.locator("#business-boundary").is_visible()
        assert "外部真实出票数未知" in page.locator("#business-boundary").inner_text()
        assert "实际出票" not in page.locator("body").inner_text()
        assert page.locator(".business-evidence").count() == 2
        options = page.locator("#case option").evaluate_all("els => els.map(e => e.value)")
        for value in options:
            page.locator("#case").select_option(value)
            details = page.locator(".business-evidence").filter(has=page.locator("a[data-event]"))
            if details.count():
                details.first.locator("summary").click()
                link = details.first.locator("a[data-event]").first
                index, event_id = link.get_attribute("data-run"), link.get_attribute("data-event")
                link.click()
                assert event_id in page.locator(f"#run{index} .detail").inner_text()
                assert "独立外部核验：未执行" in details.first.inner_text()
                break
        else:
            raise AssertionError("Expected at least one callback source link")
        page.screenshot(path=str(args.output / "desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(args.output / "mobile.png"), full_page=True)
        page.locator("#search").fill("no-such-case-business-evidence-check")
        assert page.locator(".business-evidence").count() == 0
        page.locator("#reset").click()
        assert page.locator(".business-evidence").count() == 2
        browser.close()
    assert not errors, errors
    (args.output / "validation.json").write_text(json.dumps({"passed": True, "page_errors": errors,
        "checks": ["explicit unverified state", "per-run provenance", "callback source navigation",
                   "no stale sources after filtering", "mobile overflow"], "input": str(args.html)},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("Business evidence browser checks passed")


if __name__ == "__main__":
    main()
