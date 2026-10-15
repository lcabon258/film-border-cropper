"""Meaningful IO, persistence and batch-isolation checks (stdlib unittest)."""
import sys,io,json,time,zipfile,unittest,tempfile,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'app'))
import importlib.util
if importlib.util.find_spec('cv2') is None:sys.path.insert(0,str(ROOT.parents[1]/'work/python_packages'))
import cv2,numpy as np
from PIL import Image,ImageCms
from image_ops import encode_output,load_source,crop_item,encode_tiff,effective_quad
from server import Store

class ImageTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.root=Path(self.temp.name)
  ex=Image.Exif();ex[271]='Film Test Camera';ex[274]=6;ex[306]='2026:10:15 12:00:00';ex[34853]={1:'N',2:(25.0,2.0,3.0),3:'E',4:(121.0,30.0,1.0)}
  self.meta={'exif':ex.tobytes(),'icc':ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes(),'dpi':[300,300],'xmp':b'','orientation_original':1}
  self.im=np.arange(100*150*3,dtype=np.uint16).reshape(100,150,3)
 def tearDown(self):self.temp.cleanup()
 def test_rgb16_tiff_and_png_exact_roundtrip_with_icc_dpi(self):
  for fmt in ['tiff','png']:
   b,ext,warnings=encode_output(self.im,self.meta,{'format':fmt});p=self.root/('image'+ext);p.write_bytes(b)
   actual,meta=load_source(p);self.assertEqual(actual.dtype,np.uint16);np.testing.assert_array_equal(actual,self.im)
   self.assertEqual(meta['icc'],self.meta['icc']);self.assertAlmostEqual(meta['dpi'][0],300,delta=.1)
   with Image.open(p) as image:
    self.assertEqual(image.getexif().get(274),1);self.assertEqual(image.getexif().get_ifd(34853)[1],'N')
 def test_grayscale16_and_alpha16_preserved(self):
  for im in [self.im[:,:,0],np.dstack([self.im,np.linspace(100,65535,100*150,dtype=np.uint16).reshape(100,150)])]:
   for fmt in ['png','tiff']:
    data,ext,_=encode_output(im,{}, {'format':fmt});p=self.root/('image'+ext);p.write_bytes(data);actual,_=load_source(p);np.testing.assert_array_equal(actual,im)
 def test_jpeg_explicit_bit_depth_and_metadata(self):
  with self.assertRaises(ValueError):encode_output(self.im,self.meta,{'format':'jpeg'})
  data,ext,_=encode_output(self.im,self.meta,{'format':'jpeg','bit_depth':'8','quality':100});p=self.root/'image.jpg';p.write_bytes(data);im,m=load_source(p)
  self.assertEqual(im.dtype,np.uint8);self.assertEqual(m['icc'],self.meta['icc'])
  with Image.open(p) as image:self.assertEqual(image.getexif()[271],'Film Test Camera');self.assertEqual(image.getexif()[274],1);self.assertEqual(image.size,(150,100))
 def test_exif_orientation_applied_before_coordinates(self):
  source=np.zeros((40,70,3),np.uint8);source[:,:35]=[255,0,0];p=self.root/'rotated.jpg';ex=Image.Exif();ex[274]=6;Image.fromarray(source).save(p,exif=ex)
  im,meta=load_source(p);self.assertEqual(im.shape[:2],(70,40));self.assertEqual(meta['orientation_original'],6)
 def test_single_pass_integer_and_inset_geometry(self):
  r={'width':150,'height':100,'corners':[[10,10],[130,10],[130,90],[10,90]],'inset':2,'inset_unit':'px','rotation_deg':0,'mode':'perspective'}
  actual=crop_item(self.im,r);np.testing.assert_array_equal(actual,self.im[12:88,12:128]);self.assertEqual(actual.dtype,np.uint16)
  r['inset']=100
  with self.assertRaises(ValueError):effective_quad(r)

class ProjectTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.root=ROOT/'test_workspace';cls.root.mkdir(exist_ok=True);cls.store=Store(cls.root/str(time.time_ns()))
  rng=np.random.default_rng(17);im=np.full((800,1200,3),15,np.uint8);im[40:760,40:1160]=np.clip(rng.normal(120,5,(720,1120,3)),0,255).astype(np.uint8)
  cls.raw=cv2.imencode('.png',im)[1].tobytes();cls.item=cls.store.import_file('test.png','folder/test.png',cls.raw)['item'];cls.wait()
 @classmethod
 def wait(cls):
  end=time.time()+60
  while any(j['status'] in ['queued','running'] for j in cls.store.jobs.values()):
   if time.time()>end:raise AssertionError('job timeout')
   time.sleep(.05)
 def test_import_duplicate_and_source_integrity(self):
  original=self.store.source(self.item).read_bytes();again=self.store.import_file('duplicate.png','',self.raw);self.assertTrue(again['duplicate']);self.assertEqual(original,self.raw)
 def test_confirmation_edit_invalidation_and_reload(self):
  id=self.item['id'];self.store.patch(id,{'confirmed':True});self.assertTrue(self.store.item(id)['confirmed']);self.store.patch(id,{'inset':2});self.assertFalse(self.store.item(id)['confirmed']);self.store.patch(id,{'confirmed':True})
  saved=json.loads(self.store.path.read_text(encoding='utf-8'));self.assertTrue(saved['items'][0]['confirmed']);loaded=self.store.validate_project(saved);self.assertEqual(loaded['items'][0]['inset'],2)
  reopened=Store(self.store.root);self.assertTrue(reopened.item(id)['confirmed']);self.assertEqual(reopened.item(id)['inset'],2);self.assertTrue(any(j.get('download') and j.get('outputs') for j in reopened.jobs.values()));reopened.queue.put(None)
 def test_batch_export_failure_isolation_no_overwrite(self):
  im=np.full((100,150,3),13000,np.uint16);raw=encode_tiff(im,{});r=self.store.import_file('16bit.tif','',raw)['item'];self.wait();self.store.patch(r['id'],{'corners':[[3,3],[145,3],[145,95],[3,95]],'confirmed':True});self.store.patch(self.item['id'],{'confirmed':True})
  sha_before={a['id']:hashlib.sha256(self.store.source(a).read_bytes()).hexdigest() for a in self.store.state['items']}
  job=self.store.create_job('export',[self.item['id'],r['id']],{'format':'jpeg','quality':95,'bit_depth':'preserve'});self.wait();result=self.store.jobs[job['id']];self.assertEqual(len(result['outputs']),1);self.assertEqual(len(result['errors']),1);self.assertTrue(result['download'])
  with zipfile.ZipFile(self.store.root/'Cropped'/job['id']/'Cropped.zip') as z:self.assertIsNone(z.testzip());self.assertIn('export_report.csv',z.namelist())
  for a in self.store.state['items']:self.assertEqual(hashlib.sha256(self.store.source(a).read_bytes()).hexdigest(),sha_before[a['id']])
  converted=self.store.create_job('export',[r['id']],{'format':'jpeg','quality':95,'bit_depth':'8'});self.wait();record=self.store.jobs[converted['id']]['outputs'][0];self.assertEqual(record['dtype'],'uint8');dest=self.store.root/'Cropped'/converted['id']/record['name'];self.assertEqual(json.loads(dest.with_suffix('.jpg.json').read_text(encoding='utf-8'))['output_dtype'],'uint8');self.assertEqual(load_source(dest)[0].dtype,np.uint8)
 def test_invalid_project_path_and_geometry_rejected(self):
  saved=json.loads(self.store.path.read_text(encoding='utf-8'));saved['items'][0]['source']='../outside.jpg'
  with self.assertRaises(ValueError):self.store.validate_project(saved)
  self.store.patch(self.item['id'],{'rejected':True});self.assertEqual(self.store.item(self.item['id'])['review_decision'],'abstention_appropriate');self.store.patch(self.item['id'],{'inset':1});self.assertIsNone(self.store.item(self.item['id'])['review_decision'])
  with self.assertRaises(ValueError):self.store.patch(self.item['id'],{'corners':[[0,0],[100,100],[100,0],[0,100]]})
  with self.assertRaises(ValueError):self.store.import_file('bad.jpg','',b'not an image')

if __name__=='__main__':
 suite=unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]);result=unittest.TextTestRunner(verbosity=2).run(suite)
 report={'tests':result.testsRun,'failures':[(str(t),s) for t,s in result.failures],'errors':[(str(t),s) for t,s in result.errors],'passed':result.wasSuccessful()};(ROOT/'TEST_RESULTS.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');sys.exit(0 if result.wasSuccessful() else 1)

