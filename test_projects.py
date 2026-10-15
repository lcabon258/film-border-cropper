import sys,json,time,types,unittest,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'app'))
import importlib.util
if importlib.util.find_spec('cv2') is None:sys.path.insert(0,str(ROOT.parents[1]/'work/python_packages'))
import cv2,numpy as np
from server import Store,atomic_json,MAX_UPLOAD
from image_ops import SUPPORTED
from project_manager import change_project,scan_photos

class ProjectManagementTests(unittest.TestCase):
 def setUp(self):
  self.root=ROOT/'project_test_workspace'/str(time.time_ns());self.root.mkdir(parents=True);self.app=self.root/'application';self.app.mkdir();self.old=Store(self.root/'舊專案');self.server=types.SimpleNamespace(store=self.old,token='test-token',server_port=54321)
 def tearDown(self):
  self.wait(self.server.store);self.server.store.queue.put(None)
 def wait(self,store):
  end=time.time()+30
  while any(j['status'] in ['queued','running'] for j in store.jobs.values()):
   self.assertLess(time.time(),end);time.sleep(.02)
 def change(self,kind,answer):return change_project(self.server,kind,Store,self.app,SUPPORTED,MAX_UPLOAD,atomic_json,picker=lambda *args:answer)
 def source(self):
  source=self.root/'台北底片 01';source.mkdir();(source/'sub').mkdir();im=np.full((400,600,3),12,np.uint8);im[20:380,20:580]=140;raw=cv2.imencode('.png',im)[1].tobytes();(source/'a.png').write_bytes(raw);(source/'sub'/'duplicate.png').write_bytes(raw);(source/'bad.jpg').write_bytes(b'bad');(source/'notes.txt').write_text('not a photo',encoding='utf-8');return source
 def test_new_project_names_after_source_imports_background_and_preserves_sources(self):
  source=self.source();before={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()};target=self.root/'新專案保存位置';target.mkdir();destination=target/source.name
  r=self.change('new',{'source':str(source),'workspace':str(destination)});new=self.server.store;self.wait(new)
  self.assertEqual(new.state['name'],source.name);self.assertEqual(new.root,destination.resolve());self.assertEqual(len(new.state['items']),1);self.assertEqual(r['import_job']['total'],3)
  report=json.loads((new.root/'folder_import_report.json').read_text(encoding='utf-8'));self.assertEqual(len(report['errors']),1);self.assertEqual(report['duplicates'],1)
  self.assertEqual({p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()},before)
  self.assertEqual(json.loads((self.app/'last_project.json').read_text(encoding='utf-8'))['workspace'],str(destination.resolve()))
 def test_open_restores_name_geometry_confirmation_and_keeps_old_project(self):
  other=Store(self.root/'既有專案');im=np.full((400,600,3),15,np.uint8);im[20:380,20:580]=130;row=other.import_file('image.png','',cv2.imencode('.png',im)[1].tobytes())['item'];self.wait(other);other.patch(row['id'],{'corners':[[20,20],[580,20],[580,380],[20,380]],'confirmed':True});other.state['name']='我的第三卷';other.save();other.queue.put(None)
  old_bytes=self.old.path.read_bytes();r=self.change('open',{'workspace':str(other.root)});new=self.server.store
  self.assertEqual(new.state['name'],'我的第三卷');self.assertTrue(new.item(row['id'])['confirmed']);self.assertEqual(new.item(row['id'])['corners'],[[20,20],[580,20],[580,380],[20,380]])
  self.assertEqual(json.loads(self.old.path.read_text(encoding='utf-8'))['project_id'],json.loads(old_bytes)['project_id']);self.assertFalse(r['cancelled'])
 def test_cancel_leaves_current_project_and_files_unchanged(self):
  before=self.old.path.read_bytes();r=self.change('open',{'cancelled':True});self.assertTrue(r['cancelled']);self.assertIs(self.server.store,self.old);self.assertEqual(before,self.old.path.read_bytes());self.assertFalse((self.app/'last_project.json').exists())
 def test_existing_destination_never_overwritten(self):
  source=self.source();destination=self.root/'existing';destination.mkdir();sentinel=destination/'keep.txt';sentinel.write_text('keep')
  with self.assertRaises(ValueError):self.change('new',{'source':str(source),'workspace':str(destination)})
  self.assertEqual(sentinel.read_text(),'keep');self.assertIs(self.server.store,self.old)
 def test_source_nested_destination_rejected_without_modifying_source(self):
  source=self.source();destination=source/'Project'
  with self.assertRaises(ValueError):self.change('new',{'source':str(source),'workspace':str(destination)})
  self.assertFalse(destination.exists());self.assertIs(self.server.store,self.old)
 def test_missing_or_invalid_project_does_not_replace_current(self):
  destination=self.root/'invalid';destination.mkdir()
  with self.assertRaises(ValueError):self.change('open',{'workspace':str(destination)})
  (destination/'Film_Project.json').write_text('invalid json')
  with self.assertRaises(ValueError):self.change('open',{'workspace':str(destination)})
  self.assertIs(self.server.store,self.old)
 def test_busy_project_does_not_open_picker_or_switch(self):
  called=[];self.old.jobs['running']={'status':'running'}
  try:
   with self.assertRaises(ValueError):change_project(self.server,'open',Store,self.app,SUPPORTED,MAX_UPLOAD,atomic_json,picker=lambda *args:called.append(True))
   self.assertFalse(called);self.assertIs(self.server.store,self.old)
  finally:self.old.jobs.pop('running')
 def test_empty_source_does_not_create_project(self):
  source=self.root/'empty';source.mkdir();target=self.root/'target'
  with self.assertRaises(ValueError):self.change('new',{'source':str(source),'workspace':str(target)})
  self.assertFalse(target.exists())
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]));(ROOT/'PROJECT_TEST_RESULTS.json').write_text(json.dumps({'tests':result.testsRun,'passed':result.wasSuccessful(),'failures':[s for t,s in result.failures],'errors':[s for t,s in result.errors],'scope':'Project operations with injected picker choices; native Windows dialogs compiled but not operated in this environment.'},ensure_ascii=False,indent=2),encoding='utf-8');sys.exit(0 if result.wasSuccessful() else 1)
