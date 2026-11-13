"""Exercise unknown-root and unpaired-result presentation in a real browser."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright
from invoice_eval.loader import load_cases,write_jsonl,write_json,source_hash
from invoice_eval.pipeline import ROOT,evaluate
from invoice_eval.timeline import render_timeline

parser=argparse.ArgumentParser()
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
args.output.mkdir(parents=True,exist_ok=False)
cases=load_cases(next((ROOT/'material').glob('02*.jsonl')))
unpaired=cases[1].model_copy(deep=True)
unpaired.runs[1].invoice_created=False
unknown=cases[0].model_copy(deep=True)
run=unknown.runs[0]
callback=cases[1].runs[1].events[8].model_copy(deep=True)
callback.event_id='unlocated-service-success'
callback.task_id=run.task_id;callback.trace_id=run.trace_id
run.events.append(callback);run.invoice_created=True;run.final_state='SUCCEEDED'
for name,case in [('unpaired',unpaired),('unknown-root',unknown)]:
    path=args.output/(name+'.jsonl');write_jsonl(path,[case.model_dump()])
    evaluate(path,ROOT/'configs/default.json',args.output/name)
    render_timeline(args.output/name,args.output/(name+'.html'))

errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto((args.output/'unpaired.html').resolve().as_uri())
    assert '质量分' in page.locator('#run0').inner_text()
    assert '待复核' in page.locator('#run1').inner_text()
    assert page.locator('.stat').first.inner_text()=='1 / 2'
    page.goto((args.output/'unknown-root.html').resolve().as_uri())
    assert 'INSUFFICIENT_EVIDENCE' in page.locator('#run0 .roots').inner_text()
    assert '首错 未定位' in page.locator('#run0 .roots').inner_text()
    assert page.locator('#run0 .roots [data-event]').count()==0
    page.screenshot(path=str(args.output/'unknown-root.png'),full_page=True)
    browser.close()
assert not errors,errors
write_json(args.output/'browser-validation.json',{'source_hash':source_hash(),'passed':True,'page_errors':errors,'checks':['valid unpaired run remains visible','unresolved counterpart remains pending','coverage includes individual result','unknown first fault has no dead event navigation']})
print('Gap browser checks passed')
