"""Loopback-only local application; no external service or model is required."""
from __future__ import annotations
import argparse,csv,hashlib,io,json,os,queue,secrets,sys,threading,time,traceback,urllib.parse,webbrowser,zipfile
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from datetime import datetime
import cv2,numpy as np
from image_ops import SUPPORTED,load_source,jpeg_bytes,crop_item,encode_output,validate_quad,effective_quad
from film_border_core import detect
from project_manager import change_project,owned_session_remove

APP=Path(__file__).resolve().parent
CORE_SHA=hashlib.sha256((APP/'film_border_core.py').read_bytes()).hexdigest()
MAX_UPLOAD=512*1024*1024

def atomic_json(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.tmp')
    with temp.open('w',encoding='utf-8') as f:json.dump(obj,f,ensure_ascii=False,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)

def safe_name(name):
    name=str(name).replace('\\','/').split('/')[-1];name=''.join(c for c in name if c not in '<>:"/\\|?*' and ord(c)>=32).strip(' .')
    if not name or len(name)>180:name='photo'+Path(name).suffix[:8]
    return name

class Store:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        for n in ['Originals','Previews','Cropped']: (self.root/n).mkdir(exist_ok=True)
        self.lock=threading.RLock();self.jobs={};self.queue=queue.Queue();self.closing=False
        self.path=self.root/'Film_Project.json';self.state={'schema_version':1,'application':'Film Border Cropper V1 preview','core_sha256':CORE_SHA,'project_id':secrets.token_hex(8),'name':'我的底片專案','items':[],'settings':{'format':'tiff','quality':95,'bit_depth':'preserve','tiff_compress':True},'updated_at':None}
        if self.path.exists():
            try:
                saved=json.loads(self.path.read_text(encoding='utf-8'));self.state=self.validate_project(saved)
                for item in self.state['items']:
                    if item.get('status') in ['queued','detecting']:item['status']='interrupted';item['error']='上次辨識中斷，可重新辨識。'
            except Exception as e:raise ValueError('專案檔無法讀取，原檔未修改：'+str(e))
        self.restore_exports();self.save();self.worker=threading.Thread(target=self.work,daemon=True);self.worker.start()
    def restore_exports(self):
        directory=(self.root/'Cropped').resolve()
        for folder in sorted(directory.iterdir(),key=lambda p:p.stat().st_mtime):
            if not folder.is_dir() or not folder.name.isalnum() or not folder.resolve().is_relative_to(directory):continue
            try:
                job=json.loads((folder/'export_report.json').read_text(encoding='utf-8'))
                if job.get('id')!=folder.name or job.get('kind')!='export' or job.get('status')!='completed':continue
                job['download']=(folder/'Cropped.zip').is_file();self.jobs[job['id']]=job
            except (OSError,ValueError,TypeError):continue
    def source(self,item):
        rel=item.get('source','');p=(self.root/rel).resolve()
        if not p.is_relative_to((self.root/'Originals').resolve()):raise ValueError('來源必須位於這個專案的 Originals。')
        return p
    def validate_project(self,s):
        if not isinstance(s,dict) or s.get('schema_version')!=1 or not isinstance(s.get('items'),list):raise ValueError('不是支援的專案 JSON。')
        if len(s['items'])>10000:raise ValueError('專案照片數過多。')
        ids=set()
        for r in s['items']:
            if not isinstance(r,dict) or not isinstance(r.get('id'),str) or not r['id'].isalnum() or r['id'] in ids:raise ValueError('照片識別碼無效或重複。')
            ids.add(r['id']);p=self.source(r)
            if r.get('corners') is not None:validate_quad(r['corners'],int(r['width']),int(r['height']))
            if not p.is_file():r['status']='missing';r['error']='找不到專案原始照片，請放回 Originals。';r['confirmed']=False
            elif hashlib.sha256(p.read_bytes()).hexdigest()!=r.get('sha256'):r['status']='missing';r['error']='來源校驗碼不符，請重新匯入正確原檔。';r['confirmed']=False
        s.setdefault('settings',self.state['settings'] if hasattr(self,'state') else {'format':'tiff','quality':95,'bit_depth':'preserve'});s.setdefault('name','我的底片專案')
        return s
    def save(self):
        with self.lock:self.state['updated_at']=datetime.now().isoformat(timespec='seconds');atomic_json(self.path,self.state)
    def snapshot(self):
        with self.lock:return json.loads(json.dumps({'project':self.state,'jobs':list(self.jobs.values()),'queue_size':self.queue.qsize(),'version':'1.0-preview.4','storage':{'project_root':str(self.root),'originals':str(self.root/'Originals'),'previews':str(self.root/'Previews'),'outputs':str(self.root/'Cropped')}}))
    def item(self,id):
        for r in self.state['items']:
            if r['id']==id:return r
        raise ValueError('找不到照片。')
    def create_job(self,kind,ids,settings=None):
        with self.lock:
            for id in ids:self.item(id)
            job={'id':secrets.token_hex(6),'kind':kind,'status':'queued','done':0,'total':len(ids),'errors':[],'outputs':[]}
            self.jobs[job['id']]=job;self.queue.put((job['id'],list(ids),settings or {}));return job.copy()
    def import_file(self,name,relative,raw):
        name=safe_name(name)
        if Path(name).suffix.lower() not in SUPPORTED:raise ValueError('只接受 JPEG、PNG、TIFF。')
        digest=hashlib.sha256(raw).hexdigest()
        with self.lock:
            for r in self.state['items']:
                if r['sha256']==digest:return {'item':r,'duplicate':True}
            id=secrets.token_hex(8);source=self.root/'Originals'/(id+'_'+name)
            with source.open('xb') as f:f.write(raw)
            try:im,meta=load_source(source)
            except Exception as e:
                source.unlink(missing_ok=True)
                raise ValueError('影像無法讀取：'+name) from e
            h,w=im.shape[:2]
            row={'id':id,'name':name,'relative_name':str(relative)[:400],'source':source.relative_to(self.root).as_posix(),'sha256':digest,'width':w,'height':h,'bit_depth':16 if im.dtype==np.uint16 else 8,'channels':1 if im.ndim==2 else im.shape[2],'exif_orientation_original':meta['orientation_original'],'metadata':{'icc':bool(meta['icc']),'exif':bool(meta['exif']),'dpi':meta['dpi']},'corners':None,'detection':None,'confidence':'pending','score':None,'status':'queued','confirmed':False,'auto_pass':False,'edit_revision':0,'inset':0,'inset_unit':'px','rotation_deg':0,'mode':'perspective','error':None}
            (self.root/'Previews'/(id+'.jpg')).write_bytes(jpeg_bytes(im));self.state['items'].append(row);self.save();self.create_job('detect',[id]);return {'item':row,'duplicate':False}
    def remove_items(self,ids):
        if not isinstance(ids,list) or not ids or any(not isinstance(id,str) for id in ids):
            raise ValueError('請選擇要移除的照片。')
        ids=list(dict.fromkeys(ids))
        with self.lock:
            if any(j['status'] in ['queued','running'] for j in self.jobs.values()):
                raise ValueError('請等待匯入、辨識或輸出完成後，再移除照片。')
            rows=[self.item(id) for id in ids]
            files=[]
            for row in rows:
                for folder,name in [('Originals',Path(row['source']).name),('Previews',row['id']+'.jpg')]:
                    directory=self.root/folder
                    file=directory/name
                    if directory.is_symlink() or not directory.resolve().is_relative_to(self.root) or file.is_symlink() or not file.resolve().is_relative_to(directory.resolve()):
                        raise ValueError('照片路徑不安全，未移除任何照片。')
                    if folder=='Originals' and file.resolve()!=self.source(row):
                        raise ValueError('照片路徑無效，未移除任何照片。')
                    if file.exists():
                        if not file.is_file():raise ValueError('照片路徑不是檔案。')
                        files.append((file,Path(folder)/name))
            recovery=self.root/'Removed'
            if recovery.is_symlink() or not recovery.resolve().is_relative_to(self.root):
                raise ValueError('移除備份路徑不安全。')
            backup=recovery/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+secrets.token_hex(6))
            backup.mkdir(parents=True,exist_ok=False)
            before=json.loads(json.dumps(self.state));moved=[]
            try:
                atomic_json(backup/'removed_items.json',{'project_id':self.state['project_id'],'removed_at':datetime.now().isoformat(),'items':rows})
                for source,relative in files:
                    dest=backup/relative;dest.parent.mkdir(parents=True,exist_ok=True)
                    source.rename(dest);moved.append((source,dest))
                removed=set(ids);self.state['items']=[r for r in self.state['items'] if r['id'] not in removed]
                self.save()
            except Exception:
                self.state=before
                for source,dest in reversed(moved):dest.rename(source)
                raise
            return {'removed_ids':ids,'count':len(ids),'recovery_folder':str(backup)}
    def patch(self,id,patch):
        allowed={'corners','inset','inset_unit','rotation_deg','mode','confirmed','rejected','reset'}
        if set(patch)-allowed:raise ValueError('不支援的編輯欄位。')
        with self.lock:
            r=self.item(id);candidate=dict(r)
            if patch.get('reset'):
                candidate.update({'corners':r.get('detection',{}).get('corners') if r.get('detection') else None,'inset':0,'rotation_deg':0,'confirmed':False,'edit_revision':0,'error':None,'review_decision':None,'status':'ready' if r.get('detection') else 'rejected','auto_pass':bool((r.get('detection') or {}).get('automatic_crop_allowed'))})
            else:
                geom={k:v for k,v in patch.items() if k in ['corners','inset','inset_unit','rotation_deg','mode']};candidate.update(geom)
                if geom:candidate.update({'confirmed':False,'auto_pass':False,'edit_revision':r.get('edit_revision',0)+1,'status':'manual','error':None,'review_decision':None})
                if patch.get('rejected'):candidate.update({'confirmed':False,'auto_pass':False,'status':'rejected','review_decision':'abstention_appropriate'})
                if 'confirmed' in patch:
                    if patch['confirmed'] and candidate.get('corners') is None:raise ValueError('請先建立裁切框。')
                    candidate['review_decision']='boundary_accepted' if patch['confirmed'] else None;candidate['confirmed']=bool(patch['confirmed']);candidate['status']='confirmed' if patch['confirmed'] else 'manual'
            if candidate.get('inset_unit') not in ['px','percent'] or candidate.get('mode') not in ['rotate','perspective']:raise ValueError('幾何設定無效。')
            if candidate.get('corners') is not None:effective_quad(candidate)
            if candidate.get('status')=='missing':raise ValueError('來源照片遺失。')
            r.clear();r.update(candidate);self.save();return r.copy()
    def work(self):
        while True:
            task=self.queue.get()
            if task is None:return
            jid,ids,settings=task
            with self.lock:job=self.jobs[jid];job['status']='running'
            for id in ids:
                try:
                    with self.lock:item=json.loads(json.dumps(self.item(id)));revision=item.get('edit_revision',0)
                    source=self.source(item)
                    if not source.is_file():raise ValueError('來源照片遺失。')
                    if job['kind']=='detect':
                        with self.lock:self.item(id)['status']='detecting'
                        im,meta=load_source(source)
                        try:d=detect(im,inset_px=0);error=None
                        except Exception as e:d=None;error=str(e)
                        with self.lock:
                            r=self.item(id);r['detection']=d
                            if r.get('edit_revision',0)==revision:
                                r.update({'review_decision':None,'error':error,'corners':d['corners'] if d else None,'confidence':d['confidence_level_provisional'] if d else 'rejected','score':d['confidence_heuristic'] if d else None,'status':'ready' if d else 'rejected','confirmed':False,'auto_pass':bool(d and d['automatic_crop_allowed'])})
                            else:r['status']='manual' if not r.get('confirmed') else 'confirmed'
                            if error:job['errors'].append({'id':id,'name':r['name'],'error':error})
                            self.save()
                    else:
                        if not (item.get('confirmed') or item.get('auto_pass')) or item['status'] in ['rejected','missing']:
                            raise ValueError('尚未確認，或此張已拒絕輸出。')
                        if hashlib.sha256(source.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('原檔校驗碼改變，已停止此張輸出。')
                        im,meta=load_source(source);cropped=crop_item(im,item);encoded,ext,warnings=encode_output(cropped,meta,settings)
                        output_dtype='uint8' if settings.get('bit_depth')=='8' else str(cropped.dtype)
                        destdir=self.root/'Cropped'/jid;destdir.mkdir(parents=True,exist_ok=True);dest=destdir/(safe_name(Path(item['name']).stem)+'_'+id[:6]+ext)
                        with dest.open('xb') as f:f.write(encoded)
                        sidecar={'schema_version':1,'source':item['source'],'sha256':item['sha256'],'item_id':id,'corners':item['corners'],'effective_corners':effective_quad(item).tolist(),'inset':item['inset'],'inset_unit':item['inset_unit'],'rotation_deg':item['rotation_deg'],'mode':item['mode'],'human_confirmed':item['confirmed'],'settings':settings,'output_shape':list(cropped.shape),'output_dtype':output_dtype,'source_dtype':str(im.dtype),'warnings':warnings,'core_sha256':CORE_SHA}
                        atomic_json(dest.with_suffix(ext+'.json'),sidecar)
                        with self.lock:job['outputs'].append({'id':id,'name':dest.name,'width':cropped.shape[1],'height':cropped.shape[0],'dtype':output_dtype,'warnings':warnings})
                except Exception as e:
                    with self.lock:
                        job['errors'].append({'id':id,'name':item.get('name',id) if 'item' in locals() else id,'error':str(e)})
                        if job['kind']=='detect':
                            try:self.item(id).update({'status':'error','error':str(e),'auto_pass':False})
                            except Exception:pass
                finally:
                    with self.lock:job['done']+=1
            with self.lock:
                if job['kind']=='export':
                    destdir=self.root/'Cropped'/jid;destdir.mkdir(parents=True,exist_ok=True);atomic_json(destdir/'export_report.json',{**job,'status':'completed'})
                    with (destdir/'export_report.csv').open('w',encoding='utf-8-sig',newline='') as f:
                        writer=csv.DictWriter(f,fieldnames=['id','name','status','error']);writer.writeheader();writer.writerows([{'id':o['id'],'name':o['name'],'status':'exported'} for o in job['outputs']]);writer.writerows([{**e,'status':'failed'} for e in job['errors']])
                    self.save();(destdir/'Film_Project.json').write_bytes(self.path.read_bytes())
                    target=destdir/'Cropped.zip'
                    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
                        for p in destdir.iterdir():
                            if p!=target and p.is_file():z.write(p,p.name)
                    job['download']=True
                job['status']='completed';self.save()
            self.queue.task_done()

class Handler(BaseHTTPRequestHandler):
    server_version='FilmBorderCropper/1-preview'
    def log_message(self,*args):pass
    @property
    def store(self):return self.server.store
    def send(self,status,data,ctype='application/json; charset=utf-8',headers=None):
        if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
        if isinstance(data,str):data=data.encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass
    def stream_file(self,path,ctype,filename):
        self.send_response(200);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(path.stat().st_size));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Disposition','attachment; filename="'+filename+'"');self.end_headers()
        try:
            with path.open('rb') as f:
                while True:
                    chunk=f.read(1024*1024)
                    if not chunk:break
                    self.wfile.write(chunk)
        except (BrokenPipeError,ConnectionResetError):pass
    def route(self):return urllib.parse.urlsplit(self.path)
    def authorized(self):
        parsed=self.route();query=urllib.parse.parse_qs(parsed.query);token=self.headers.get('X-Film-Token') or query.get('token',[''])[0]
        origin=self.headers.get('Origin');expected=f'http://127.0.0.1:{self.server.server_port}'
        return secrets.compare_digest(token,self.server.token) and (not origin or origin==expected)
    def read_body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n<0 or n>MAX_UPLOAD:raise ValueError('單檔大小上限 512 MB。')
        raw=self.rfile.read(n)
        if len(raw)!=n:raise ValueError('上傳未完成。')
        return raw
    def json_body(self):return json.loads(self.read_body().decode('utf-8-sig'))
    def do_GET(self):
        store=self.store
        parsed=self.route();path=parsed.path;query=urllib.parse.parse_qs(parsed.query)
        if path in ['/static/app.css','/static/app.js']:
            p=APP/'static'/path.split('/')[-1];self.send(200,p.read_bytes(),'text/css; charset=utf-8' if p.suffix=='.css' else 'text/javascript; charset=utf-8');return
        if not self.authorized():self.send(403,{'error':'這個頁面的工作階段已失效，請從啟動程式重新開啟。'});return
        try:
            if path=='/':self.send(200,(APP/'static/index.html').read_text(encoding='utf-8').replace('__TOKEN__',self.server.token),'text/html; charset=utf-8')
            elif path=='/api/state':self.send(200,store.snapshot())
            elif path=='/api/project':self.send(200,store.state,headers={'Content-Disposition':'attachment; filename="Film_Project.json"'})
            elif path.startswith('/api/image/'):
                parts=path.strip('/').split('/');id=parts[2];kind=parts[3] if len(parts)>3 else 'original'
                with store.lock:item=json.loads(json.dumps(store.item(id)))
                if kind=='crop':im,meta=load_source(store.source(item));blob=jpeg_bytes(crop_item(im,item))
                else:
                    p=store.root/'Previews'/(id+'.jpg')
                    if not p.exists():im,meta=load_source(store.source(item));p.write_bytes(jpeg_bytes(im))
                    blob=p.read_bytes()
                self.send(200,blob,'image/jpeg')
            elif path.startswith('/api/download/'):
                jid=path.split('/')[-1]
                if not jid.isalnum():raise ValueError('下載識別碼無效。')
                job=store.jobs.get(jid)
                if not job or not job.get('download'):raise ValueError('輸出尚未完成。')
                p=store.root/'Cropped'/jid/'Cropped.zip';self.stream_file(p,'application/zip','Film_Cropped.zip')
            else:self.send(404,{'error':'找不到此頁面。'})
        except Exception as e:self.send(400,{'error':str(e)})
    def do_POST(self):
        if not self.authorized():self.send(403,{'error':'工作階段驗證失敗。'});return
        parsed=self.route();query=urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path not in ['/api/project/open','/api/project/new-from-folder'] and getattr(self.server,'project_lock',None) and self.server.project_lock.locked():raise ValueError('請先完成 Windows 專案選擇視窗，再進行其他操作。')
            expected=self.headers.get('X-Film-Project')
            if expected and expected!=self.store.state['project_id']:raise ValueError('專案已切換，請重新整理此頁後再操作。')
            if parsed.path in ['/api/project/open','/api/project/new-from-folder']:
                self.json_body()
                if not hasattr(self.server,'project_lock'):self.server.project_lock=threading.Lock()
                if not self.server.project_lock.acquire(blocking=False):raise ValueError('另一個專案選擇視窗正在開啟，請先完成。')
                try:
                    kind='open' if parsed.path.endswith('/open') else 'new'
                    result=change_project(self.server,kind,Store,APP.parent,SUPPORTED,MAX_UPLOAD,atomic_json)
                    self.send(200,result)
                finally:self.server.project_lock.release()
            elif parsed.path=='/api/import':
                name=query.get('name',['photo.jpg'])[0];relative=query.get('relative',[name])[0];self.send(200,self.store.import_file(name,relative,self.read_body()))
            elif parsed.path=='/api/items/remove':
                self.send(200,self.store.remove_items(self.json_body().get('ids')))
            elif parsed.path.startswith('/api/item/'):
                self.send(200,{'item':self.store.patch(parsed.path.split('/')[-1],self.json_body())})
            elif parsed.path=='/api/detect':
                body=self.json_body();self.send(200,self.store.create_job('detect',body['ids']))
            elif parsed.path=='/api/batch':
                body=self.json_body();results=[]
                for id in body['ids']:
                    try:results.append({'id':id,'ok':True,'item':self.store.patch(id,body['patch'])})
                    except Exception as e:results.append({'id':id,'ok':False,'error':str(e)})
                self.send(200,{'results':results})
            elif parsed.path=='/api/settings':
                body=self.json_body();allowed={'format','quality','bit_depth','tiff_compress'}
                if set(body)-allowed:raise ValueError('不支援的輸出設定。')
                with self.store.lock:self.store.state['settings'].update(body);self.store.save()
                self.send(200,{'ok':True})
            elif parsed.path=='/api/export':
                body=self.json_body();settings={**self.store.state['settings'],**body.get('settings',{})};self.send(200,self.store.create_job('export',body['ids'],settings))
            elif parsed.path=='/api/project/load':
                body=self.json_body()
                with self.store.lock:
                    if any(j['status'] in ['queued','running'] for j in self.store.jobs.values()):raise ValueError('請等待目前工作完成，再載入專案。')
                    loaded=self.store.validate_project(body)
                    if self.store.path.exists():
                        backup=self.store.root/('Film_Project_backup_'+str(time.time_ns())+'.json');backup.write_bytes(self.store.path.read_bytes())
                    self.store.state=loaded;self.store.save();self.send(200,self.store.snapshot())
            elif parsed.path=='/api/project/name':
                body=self.json_body()
                with self.store.lock:self.store.state['name']=str(body['name'])[:120];self.store.save()
                self.send(200,{'ok':True})
            elif parsed.path=='/api/shutdown':
                self.read_body()
                if any(j['status'] in ['queued','running'] for j in self.store.jobs.values()):raise ValueError('尚有工作進行中，請完成後再關閉。')
                self.store.save();self.send(200,{'ok':True});threading.Thread(target=self.server.shutdown,daemon=True).start()
            else:self.send(404,{'error':'找不到此操作。'})
        except Exception as e:self.send(400,{'error':str(e)})

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workspace',type=Path,default=None);parser.add_argument('--port',type=int,default=0);parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
    if args.workspace is None:
        args.workspace=APP.parent/'Projects'/'MyFilm'
        try:
            remembered=Path(json.loads((APP.parent/'last_project.json').read_text(encoding='utf-8'))['workspace'])
            if remembered.is_dir() and (remembered/'Film_Project.json').is_file():args.workspace=remembered
        except (OSError,ValueError,KeyError,TypeError):pass
    store=Store(args.workspace);server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.store=store;server.project_lock=threading.Lock();server.token=secrets.token_urlsafe(32);url=f'http://127.0.0.1:{server.server_port}/?token={server.token}'
    atomic_json(store.root/'server_session.json',{'url':url,'pid':os.getpid(),'started':datetime.now().isoformat()})
    print('Film Border Cropper is ready: '+url,flush=True)
    if not args.no_browser:webbrowser.open(url)
    try:server.serve_forever(poll_interval=.25)
    finally:server.store.save();server.server_close();owned_session_remove(server.store.root,server.token)
if __name__=='__main__':main()
