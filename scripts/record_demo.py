import argparse
import html
import json
import subprocess
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
from invoice_eval.loader import source_hash,write_json
from invoice_eval.pipeline import ROOT

parser=argparse.ArgumentParser()
parser.add_argument("--output",type=Path,required=True)
parser.add_argument("--run-root",type=Path,required=True)
args=parser.parse_args()
args.output.mkdir(parents=True,exist_ok=False)
bad=args.output/"bad-input.jsonl"
bad.write_text('{"invalid":\n',encoding="utf-8")
result=subprocess.run([sys.executable,"-m","invoice_eval","evaluate","--cases",str(bad.resolve()),"--output",str((args.output/"failed-evaluation").resolve())],capture_output=True,text=True,encoding="utf-8")
assert result.returncode!=0
assert not (args.output/"failed-evaluation/submission.jsonl").exists()
write_json(args.output/"fault-check.json",{"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr,"submission_exists":False})
diagnostic=(args.output/"failed-evaluation/diagnostic.json").read_text(encoding="utf-8")
fault_html=args.output/"fault.html"
fault_html.write_text('<!doctype html><meta charset="utf-8"><title>评测器故障演示</title><body style="font:20px/1.7 system-ui;padding:60px;background:#f3f6f8"><h1>坏输入明确失败</h1><p>实际命令退出码：'+str(result.returncode)+'</p><p>没有生成 submission.jsonl；诊断及评测器 trace 已保留。</p><pre style="white-space:pre-wrap">'+html.escape(diagnostic)+'</pre>',encoding="utf-8")
start=time.perf_counter()
with sync_playwright() as p:
    browser=p.chromium.launch(channel="msedge",headless=True)
    context=browser.new_context(viewport={"width":1440,"height":960},record_video_dir=str(args.output),record_video_size={"width":1440,"height":960})
    page=context.new_page()
    page.goto((args.run_root/"train-timeline.html").resolve().as_uri())
    def caption(text):
        page.evaluate("text=>{let el=document.querySelector('#demo-caption');if(!el){el=document.createElement('div');el.id='demo-caption';el.style='position:fixed;bottom:16px;left:32px;right:32px;background:#102b3d;color:white;padding:14px 24px;z-index:1000;font:20px system-ui;border-radius:8px';document.body.append(el)}el.textContent=text}",text)
    caption("1. 正确等待确认：处理正确，尚未出票")
    page.locator('#case').select_option("INV-T01")
    page.locator('#run0 button[data-event="INV-T01-A-07"]').click()
    page.locator('#panels').scroll_into_view_if_needed()
    page.wait_for_timeout(3000)
    caption("2. 首错口径反转向下传播；未确认提交另算独立风险")
    page.locator('#run1 .root a').first.click()
    page.wait_for_timeout(3000)
    page.locator('#run1 button[data-event="INV-T01-B-08"]').click()
    caption("3. 工具保护性拒绝：不误记为工具故障")
    page.wait_for_timeout(3000)
    page.locator('#case').select_option("INV-T08")
    caption("4. 受理未知：停止重试与错误重试分别判定")
    page.locator('#run1 .root a').first.click()
    page.wait_for_timeout(3000)
    page.locator('#case').select_option("INV-T10")
    caption("5. 明确失败回调不能被 Agent 成功话术覆盖")
    page.locator('#run1 .root a').first.click()
    page.wait_for_timeout(3000)
    page.goto((args.run_root/"validation-timeline.html").resolve().as_uri())
    caption("6. 完整验证集：8 个 Case，16 条运行，分开统计处理与出票")
    page.wait_for_timeout(3000)
    page.goto(fault_html.resolve().as_uri())
    page.wait_for_timeout(3000)
    video=page.video
    context.close()
    original=Path(video.path())
    original.replace(args.output/"class3-demo.webm")
    browser.close()
write_json(args.output/"demo-validation.json",{"source_hash":source_hash(),"passed":True,"duration_seconds":round(time.perf_counter()-start,2),"fault_exit_code":result.returncode,"scenes":7})
print(args.output/"class3-demo.webm")
