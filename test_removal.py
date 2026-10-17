"""Removal safety and persistence regression checks."""
import io,json,sys,tempfile,threading,types,unittest
from pathlib import Path
from email.message import Message
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'app'))
import importlib.util
if importlib.util.find_spec('cv2') is None:sys.path.insert(0,str(ROOT.parents[1]/'work/python_packages'))
from server import Store,Handler

class RemovalTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.root=Path(self.temp.name)
  self.store=Store(self.root/'project');self.source=self.root/'external.png';self.source.write_bytes(b'original')
  for id in ['a1','a2','a3']:
   (self.store.root/'Originals'/f'{id}_photo.png').write_bytes(b'original')
   (self.store.root/'Previews'/f'{id}.jpg').write_bytes(b'preview')
   self.store.state['items'].append({'id':id,'name':'photo.png','source':f'Originals/{id}_photo.png','sha256':__import__('hashlib').sha256(b'original').hexdigest(),'corners':None,'confirmed':True})
  self.store.save();self.output=self.store.root/'Cropped'/'old.png';self.output.write_bytes(b'export')
 def tearDown(self):
  self.store.queue.put(None);self.store.worker.join(timeout=2);self.temp.cleanup()
 def test_batch_removal_preserves_external_and_exports_and_reopens(self):
  result=self.store.remove_items(['a1','a2','a1']);self.assertEqual(result['count'],2)
  self.assertEqual([r['id'] for r in self.store.state['items']],['a3'])
  backup=Path(result['recovery_folder']);self.assertEqual((backup/'Originals/a1_photo.png').read_bytes(),b'original')
  self.assertTrue((backup/'Previews/a2.jpg').exists());self.assertTrue(json.loads((backup/'removed_items.json').read_text(encoding='utf-8'))['items'][0]['confirmed'])
  self.assertFalse((self.store.root/'Originals/a1_photo.png').exists());self.assertEqual(self.source.read_bytes(),b'original');self.assertEqual(self.output.read_bytes(),b'export')
  reopened=Store(self.store.root)
  try:self.assertEqual([r['id'] for r in reopened.state['items']],['a3'])
  finally:reopened.queue.put(None);reopened.worker.join(timeout=2)
 def test_invalid_batch_never_partially_removes(self):
  for ids in [None,[],['a1','unknown'],['a1',1]]:
   with self.assertRaises(ValueError):self.store.remove_items(ids)
  self.assertEqual(len(self.store.state['items']),3);self.assertTrue((self.store.root/'Originals/a1_photo.png').exists())
 def test_busy_work_blocks_removal(self):
  for kind in ['detect','export','import']:
   for status in ['queued','running']:
    self.store.jobs={'job':{'kind':kind,'status':status}}
    with self.assertRaisesRegex(ValueError,'完成'):self.store.remove_items(['a1'])
  self.assertEqual(len(self.store.state['items']),3)
 def test_save_failure_rolls_back_files_and_records(self):
  before=self.store.path.read_bytes()
  with patch.object(self.store,'save',side_effect=OSError('disk full')):
   with self.assertRaises(OSError):self.store.remove_items(['a1','a2'])
  self.assertEqual(self.store.path.read_bytes(),before);self.assertEqual(len(self.store.state['items']),3)
  for id in ['a1','a2']:self.assertTrue((self.store.root/'Originals'/f'{id}_photo.png').exists());self.assertTrue((self.store.root/'Previews'/f'{id}.jpg').exists())
 def test_missing_files_can_be_removed(self):
  (self.store.root/'Originals/a1_photo.png').unlink();(self.store.root/'Previews/a1.jpg').unlink()
  self.assertEqual(self.store.remove_items(['a1'])['count'],1)
 def test_move_failure_rolls_back_prior_moves(self):
  rename=Path.rename
  def fail_preview(source,target):
   if source.parent.name=='Previews':raise OSError('file locked')
   return rename(source,target)
  with patch.object(Path,'rename',fail_preview):
   with self.assertRaises(OSError):self.store.remove_items(['a1'])
  self.assertTrue((self.store.root/'Originals/a1_photo.png').exists());self.assertEqual(len(self.store.state['items']),3)
 def test_outside_path_is_rejected(self):
  self.store.state['items'][0]['source']='../external.png'
  with self.assertRaises(ValueError):self.store.remove_items(['a1'])
  self.assertEqual(self.source.read_bytes(),b'original');self.assertEqual(len(self.store.state['items']),3)
 def test_route_requires_auth_and_current_project(self):
  server=types.SimpleNamespace(store=self.store,token='token',server_port=54321,project_lock=threading.Lock())
  def request(auth=True,project=None):
   h=Handler.__new__(Handler);h.server=server;h.path='/api/items/remove';h.command='POST';h.request_version='HTTP/1.1';h.requestline='POST /api/items/remove HTTP/1.1';h.headers=Message()
   raw=json.dumps({'ids':['a1']}).encode();h.headers['Content-Length']=str(len(raw))
   if auth:h.headers['X-Film-Token']='token'
   if project:h.headers['X-Film-Project']=project
   h.rfile=io.BytesIO(raw);h.wfile=io.BytesIO();h.do_POST();head,body=h.wfile.getvalue().split(b'\r\n\r\n',1);return int(head.split()[1]),json.loads(body)
  self.assertEqual(request(False)[0],403);self.assertEqual(request(project='stale')[0],400)
  status,body=request(project=self.store.state['project_id']);self.assertEqual(status,200);self.assertEqual(body['removed_ids'],['a1'])

if __name__=='__main__':unittest.main(verbosity=2)
