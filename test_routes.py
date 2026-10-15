import sys,io,json,time,unittest,types
from pathlib import Path
from email.message import Message
from unittest.mock import patch
import threading
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'app'))
import importlib.util
if importlib.util.find_spec('cv2') is None:sys.path.insert(0,str(ROOT.parents[1]/'work/python_packages'))
import cv2,numpy as np
from server import Handler,Store
class RouteTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.store=Store(ROOT/'route_test_workspace'/str(time.time_ns()));cls.server=types.SimpleNamespace(store=cls.store,token='test-session-token',server_port=54321,shutdown=lambda:None)
 def request(self,method,path,body=None,authorized=True,origin=None,project=None):
  h=Handler.__new__(Handler);h.server=self.server;h.path=path;h.command=method;h.request_version='HTTP/1.1';h.requestline=method+' '+path+' HTTP/1.1';h.headers=Message();raw=body if isinstance(body,bytes) else json.dumps(body or {}).encode();h.headers['Content-Length']=str(len(raw))
  if authorized:h.headers['X-Film-Token']=self.server.token
  if origin:h.headers['Origin']=origin
  if project:h.headers['X-Film-Project']=project
  h.rfile=io.BytesIO(raw);h.wfile=io.BytesIO();getattr(h,'do_'+method)();answer=h.wfile.getvalue();head,data=answer.split(b'\r\n\r\n',1);status=int(head.split()[1]);return status,data,head
 def test_authentication_origin_and_static_page(self):
  self.assertEqual(self.request('GET','/api/state',authorized=False)[0],403);self.assertEqual(self.request('POST','/api/settings',{},origin='https://example.com')[0],403)
  status,raw,_=self.request('GET','/?token=test-session-token');self.assertEqual(status,200);self.assertIn('FRAME REVIEW',raw.decode());self.assertNotIn('__TOKEN__',raw.decode())
  self.assertEqual(self.request('GET','/static/app.js',authorized=False)[0],200)
 def test_import_edit_preview_project_and_export_routes(self):
  im=np.full((800,1200,3),12,np.uint8);im[40:760,40:1160]=130;raw=cv2.imencode('.png',im)[1].tobytes();status,blob,_=self.request('POST','/api/import?name=sample.png',raw);self.assertEqual(status,200);id=json.loads(blob)['item']['id']
  end=time.time()+30
  while any(j['status'] in ['queued','running'] for j in self.store.jobs.values()):
   self.assertLess(time.time(),end);time.sleep(.02)
  self.assertEqual(self.request('POST','/api/item/'+id,{'confirmed':True})[0],200)
  status,preview,_=self.request('GET','/api/image/'+id+'/crop');self.assertEqual(status,200);self.assertIsNotNone(cv2.imdecode(np.frombuffer(preview,np.uint8),-1))
  status,proj,_=self.request('GET','/api/project');self.assertEqual(status,200);self.assertTrue(json.loads(proj)['items'][0]['confirmed'])
  self.assertEqual(self.request('POST','/api/project/load',json.loads(proj))[0],200)
  status,job,_=self.request('POST','/api/export',{'ids':[id],'settings':{'format':'png'}});self.assertEqual(status,200);jid=json.loads(job)['id']
  while self.store.jobs[jid]['status']!='completed':self.assertLess(time.time(),end);time.sleep(.02)
  self.assertEqual(self.request('GET','/api/download/'+jid)[0],200)
  self.assertEqual(self.request('GET','/api/image/notfound/original')[0],400)
 def test_project_routes_ignore_web_paths_and_guard_stale_tabs(self):
  with patch('server.change_project',return_value={'cancelled':True}) as picker:
   status,blob,_=self.request('POST','/api/project/open',{'workspace':'untrusted-path'})
   self.assertEqual(status,200);self.assertTrue(json.loads(blob)['cancelled']);self.assertEqual(picker.call_args.args[1],'open')
   status,blob,_=self.request('POST','/api/project/new-from-folder',{})
   self.assertEqual(status,200);self.assertEqual(picker.call_args.args[1],'new')
  self.assertEqual(self.request('POST','/api/settings',{'quality':90},project='stale-project-id')[0],400)
  self.server.project_lock=threading.Lock();self.server.project_lock.acquire()
  try:self.assertEqual(self.request('POST','/api/settings',{'quality':90})[0],400)
  finally:self.server.project_lock.release()
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]));(ROOT/'ROUTE_TEST_RESULTS.json').write_text(json.dumps({'tests':result.testsRun,'passed':result.wasSuccessful(),'transport':'Handler routes exercised in-process with byte streams; TCP/browser access blocked by execution environment.'},indent=2),encoding='utf-8');sys.exit(0 if result.wasSuccessful() else 1)
