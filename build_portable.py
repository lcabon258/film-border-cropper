"""Build a portable Windows folder from the available bundled runtime."""
import sys,json,shutil,hashlib,zipfile,argparse,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser();parser.add_argument('--python-root',type=Path,default=Path(sys.base_prefix));parser.add_argument('--opencv-site',type=Path);args=parser.parse_args()
BASE=args.python_root.resolve()
cv_spec=importlib.util.find_spec('cv2')
CV=args.opencv_site or (Path(cv_spec.origin).parent.parent if cv_spec else ROOT.parents[1]/'work/python_packages')
RUNTIME=ROOT/'_internal/runtime';RUNTIME.mkdir(parents=True,exist_ok=True)
if BASE==RUNTIME.resolve():
 print('Runtime is already bundled; no copying is needed.');sys.exit(0)
IGNORE=shutil.ignore_patterns('__pycache__','*.pyc','test','tests','idlelib','ensurepip','tkinter','turtledemo','site-packages','venv')
for name in ['python.exe','pythonw.exe','python3.dll','python312.dll','vcruntime140.dll','vcruntime140_1.dll','LICENSE.txt']:shutil.copy2(BASE/name,RUNTIME/name)
shutil.copytree(BASE/'Lib',RUNTIME/'Lib',dirs_exist_ok=True,ignore=IGNORE)
shutil.copytree(BASE/'DLLs',RUNTIME/'DLLs',dirs_exist_ok=True,ignore=shutil.ignore_patterns('_test*','*_test*','_tkinter*','tcl*','tk*'))
site=RUNTIME/'Lib/site-packages';site.mkdir(exist_ok=True)
for name in ['numpy','numpy.libs','PIL']:
 shutil.copytree(BASE/'Lib/site-packages'/name,site/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','tests','test'))
shutil.copytree(CV/'cv2',site/'cv2',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','data','samples'))
(RUNTIME/'python312._pth').write_text('.\nLib\nDLLs\nLib/site-packages\n../../app\nimport site\n',encoding='ascii')
licenses=ROOT/'THIRD_PARTY_LICENSES';licenses.mkdir(exist_ok=True)
shutil.copy2(BASE/'LICENSE.txt',licenses/'Python-LICENSE.txt')
for base in [BASE/'Lib/site-packages',CV]:
 for directory in base.glob('*.dist-info'):
  if not any(n in directory.name.lower() for n in ['numpy','pillow','opencv']):continue
  for p in directory.rglob('*'):
   if p.is_file() and any(s in p.name.lower() for s in ['license','copying','notice']):
    target=licenses/directory.name/p.relative_to(directory);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
for name in ['LICENSE.txt','LICENSE-3RD-PARTY.txt']:shutil.copy2(CV/'cv2'/name,licenses/('OpenCV-'+name))
manifest={'python':sys.version,'runtime_layout':'private isolated CPython runtime; does not use system Python or user site-packages','launcher':'C# .NET Framework 4.x x64, built with Windows SDK compiler','source_core_sha256':hashlib.sha256((ROOT/'app/film_border_core.py').read_bytes()).hexdigest(),'runtime_bytes':sum(p.stat().st_size for p in RUNTIME.rglob('*') if p.is_file())}
(ROOT/'BUILD_INFO.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(manifest)
