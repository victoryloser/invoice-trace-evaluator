"""Capture the real timeline with temporary fictional input; no model calls."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"tests"))
sys.path.insert(0, str(ROOT/".build/browser-tools"))
from test_onboarding import evaluate_fictional
from invoice_eval.timeline import render_timeline
from playwright.sync_api import sync_playwright


def main():
    output = ROOT/"docs/images"
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    with tempfile.TemporaryDirectory(prefix="invoice-readme-") as folder:
        _, run = evaluate_fictional(Path(folder))
        html = render_timeline(run, Path(folder)/"timeline.html")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
            page = browser.new_page(viewport={"width":1440, "height":1100}, device_scale_factor=1)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(html.as_uri())
            assert page.locator("#case option").count() == 1
            page.locator("#run1 .root a").first.click()
            assert "SUBMIT_INVOICE" in page.locator("#run1 .detail h3").inner_text()
            page.locator("#run1 .detail details").nth(1).locator("summary").click()
            assert page.locator("#run1 .detail details").nth(1).get_attribute("open") is not None
            page.locator("#run1 .detail details").nth(1).locator("summary").click()
            page.locator("#search").fill("does-not-exist")
            assert page.locator("#case option").count() == 0
            assert not page.locator("#judges").inner_text()
            page.locator("#reset").click()
            page.locator("#filter").select_option("risk")
            assert page.locator("#case option").count() == 1
            page.locator("#reset").click()
            page.locator("#run1 .root a").first.click()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output/"timeline.png"), full_page=True)
            page.set_viewport_size({"width":390, "height":844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output/"mobile.png"), full_page=True)
            browser.close()
    assert not errors, errors
    report = {"passed":True, "data":"temporary fictional records", "model_calls":False,
              "checks":["root to first fault", "field provenance", "search", "filter", "reset", "mobile overflow"], "page_errors":errors}
    (ROOT/".build").mkdir(exist_ok=True)
    (ROOT/".build/browser-validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
