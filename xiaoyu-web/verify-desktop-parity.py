"""Browser/Qt parity for an explicitly supplied isolated release instance."""
from pathlib import Path
import argparse,json,os,sys,tempfile,time,shutil
from PySide6.QtCore import QCoreApplication,QEvent,Qt
from PySide6.QtWidgets import QApplication
from playwright.sync_api import sync_playwright


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--instance',required=True,type=Path)
 parser.add_argument('--output',required=True,type=Path)
 args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
 runtime=json.loads((args.instance/'runtime.json').read_text(encoding='utf-8'))
 assert runtime['version'].startswith('0.2.'),'Use only the isolated release, not a production instance'
 workspace=args.instance/'workspace';sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(workspace/'dafeiyu'),str(workspace/'xiaoyu-web')]
 os.environ.update(XIAOYU_WORKSPACE=str(workspace),XIAOYU_RUNTIME=str(args.instance/'runtime.json'))
 from ui.web_window import SharedWebWindow
 origin='http://127.0.0.1:'+str(runtime['ports']['web']);launch=origin+'/launch?ticket='+runtime['launch_token']
 app=QApplication.instance() or QApplication([]);profile=Path(tempfile.mkdtemp(prefix='xiaoyu-release-parity-'))
 window=SharedWebWindow(data_dir=profile,url=launch);window.resize(1360,900);window.show()
 errors=[];checks=[];report={'ok':False,'scope':'Isolated release pages; no paid generation, QQ sending or external desktop actions'}
 def wait(predicate,timeout=30):
  end=time.monotonic()+timeout
  while time.monotonic()<end:
   app.processEvents()
   if predicate():return
   time.sleep(.02)
  raise RuntimeError('Desktop rendering did not complete')
 def js(code):
  values=[];window.page.runJavaScript(code,lambda value:values.append(value));wait(lambda:bool(values));return values[0]
 def capture(path):
  # A DOM response can precede Chromium's compositor. A hidden native window
  # may never paint, even when its page is fully loaded and queryable.
  end=time.monotonic()+.6
  while time.monotonic()<end:
   app.processEvents();time.sleep(.02)
  window.repaint();app.processEvents()
  assert window.grab().save(str(path))
 try:
  wait(lambda:window._ready)
  with sync_playwright() as pw:
   browser=pw.chromium.launch(channel='msedge',headless=True)
   page=browser.new_page(viewport={'width':1360,'height':850});page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(launch);page.wait_for_selector('#nav .nav-item')
   routes=page.locator('#nav .nav-item').evaluate_all('items=>items.map(e=>({id:e.dataset.id,label:e.textContent.trim()}))')
   assert len(routes)==21,routes
   assert routes==json.loads(js("JSON.stringify(Array.from(document.querySelectorAll('#nav .nav-item'),e=>({id:e.dataset.id,label:e.textContent.trim()})))"))
   expression="JSON.stringify({title:document.querySelector('.page-title')?.textContent,buttons:Array.from(document.querySelectorAll('#view button'),e=>{const b=e.cloneNode(true);b.querySelectorAll('.tab-count').forEach(x=>x.remove());return b.textContent.trim()}).filter(Boolean),fields:Array.from(document.querySelectorAll('#view input,#view select,#view textarea'),e=>({id:e.id,type:e.type,value:e.type==='password'?'':e.type==='checkbox'?e.checked:e.value}))})"
   for entry in routes:
    route='#/'+entry['id'];page.goto(origin+'/'+route);page.wait_for_function("route=>app.prevHash===route&&!document.querySelector('#view .skeleton')&&document.querySelector('#view').innerText.trim().length>20",arg=route)
    js('location.hash='+json.dumps(route));wait(lambda:js('app.prevHash')==route and js("!document.querySelector('#view .skeleton') && document.querySelector('#view').innerText.trim().length>20"))
    # Some pages finish independent status reads after the main render.
    for attempt in range(80):
     left=json.loads(page.evaluate(expression));right=json.loads(js(expression))
     if left==right:break
     app.processEvents();time.sleep(.1)
    assert left==right,{'route':route,'browser':left,'desktop':right}
    assert page.locator('#view .error-banner').count()==0,route
    page.screenshot(path=str(args.output/(entry['id']+'-browser.png')),full_page=True)
    capture(args.output/(entry['id']+'-desktop.png'))
    checks.append({'route':route,'same_title_actions_fields':True})
   for palette in ('charcoal','sky-blue','slate-blue'):
    for route in ('welcome','chat','connections','models','migration','settings'):
     page.goto(origin+'/#/'+route);page.wait_for_function("route=>app.prevHash==='#/'+route&&!document.querySelector('#view .skeleton')&&document.querySelector('#view').innerText.trim().length>20",arg=route)
     js('location.hash='+json.dumps('#/'+route));wait(lambda:js('app.prevHash')=='#/'+route and js("!document.querySelector('#view .skeleton') && document.querySelector('#view').innerText.trim().length>20"))
     code="window.XiaoyuSettings.editingAppearance=true;window.XiaoyuThemes.apply('xiaoyu-room',false,'dark',"+json.dumps(palette)+");"
     page.evaluate('()=>{'+code+'}');js(code)
     assert page.evaluate('document.documentElement.dataset.nightPalette')==js('document.documentElement.dataset.nightPalette')==palette
     page.screenshot(path=str(args.output/(route+'-'+palette+'-browser.png')),full_page=True)
     capture(args.output/(route+'-'+palette+'-desktop.png'))
     checks.append({'route':'#/'+route,'palette':palette,'same_theme':True})
   report.update(ok=not errors,route_count=len(routes),checks=checks,page_errors=errors)
   assert not errors,errors
   browser.close()
 finally:
  report['checks']=checks
  (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  window.close();window.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()
 print(json.dumps({'ok':report['ok'],'routes':len(routes),'checks':len(checks),'page_errors':errors},ensure_ascii=False))
 return 0
if __name__=='__main__':raise SystemExit(main())
