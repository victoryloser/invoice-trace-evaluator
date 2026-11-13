import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser()
parser.add_argument("--html",type=Path,required=True)
parser.add_argument("--output",type=Path,required=True)
args=parser.parse_args()
args.output.mkdir(parents=True,exist_ok=False)
errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch(channel="msedge",headless=True)
    context=browser.new_context(viewport={"width":1440,"height":1000},record_video_dir=str(args.output/"video"))
    page=context.new_page()
    page.on("pageerror",lambda e:errors.append(str(e)))
    page.goto(args.html.resolve().as_uri())
    page.locator('#case').select_option("INV-V08")
    page.locator('#run0 .root a').first.click()
    assert "SUBMIT_INVOICE" in page.locator('#run0 .detail h3').inner_text()
    page.locator('#run0 details').nth(1).locator('summary').click()
    assert page.locator('#run0 details').nth(1).get_attribute('open') is not None
    page.locator('#search').fill("INV-V06")
    assert page.locator('#case option').count()==1
    page.locator('#filter').select_option("bad")
    assert page.locator('#case option').count()==0
    page.locator('#reset').click()
    assert page.locator('#case option').count()==8
    page.locator('#case').select_option("INV-V08")
    page.screenshot(path=str(args.output/"desktop.png"),full_page=True)
    page.set_viewport_size({"width":390,"height":844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(args.output/"mobile.png"),full_page=True)
    context.close()
    browser.close()
assert not errors,errors
(args.output/"browser-validation.json").write_text(json.dumps({"passed":True,"page_errors":errors,"checks":["case selection","root cause click to event","field provenance expansion","search","risk/error filter","reset","mobile overflow"],"input":str(args.html)},ensure_ascii=False,indent=2),encoding="utf-8")
print("Browser checks passed")
