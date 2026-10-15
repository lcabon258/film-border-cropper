"""Project selection, folder import and native Windows picker bridge."""
from __future__ import annotations
import json,os,secrets,subprocess,threading,time
from pathlib import Path

def native_pick(app_root,kind,initial):
    if kind not in ['open','new']:raise ValueError('專案操作無效。')
    app_root=Path(app_root);exe=app_root/'FilmBorderCropper.exe'
    if os.name!='nt' or not exe.is_file():raise ValueError('資料夾選擇需要 Windows 可攜版啟動器。')
    temp=app_root/'_internal/dialogs';temp.mkdir(parents=True,exist_ok=True);result=temp/(secrets.token_hex(12)+'.json')
    try:
        proc=subprocess.run([str(exe),'--dialog',kind,'--result-file',str(result),'--initial',str(initial)],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=False)
        if not result.is_file():raise ValueError('未取得資料夾選擇結果，請重試。')
        data=json.loads(result.read_text(encoding='utf-8-sig'))
        if data.get('error'):raise ValueError(data['error'])
        return data
    finally:result.unlink(missing_ok=True)

def scan_photos(source,supported):
    source=Path(source).resolve()
    if not source.is_dir():raise ValueError('找不到來源照片資料夾。')
    photos=[]
    for base,dirs,files in os.walk(source,followlinks=False):
        parent=Path(base)
        dirs[:]=[d for d in sorted(dirs) if not (parent/d).is_symlink() and not getattr(parent/d,'is_junction',lambda:False)() and (parent/d).resolve().is_relative_to(source)]
        for name in sorted(files):
            p=parent/name
            if p.suffix.lower() in supported and not p.is_symlink() and p.resolve().is_relative_to(source):
                photos.append(p)
                if len(photos)>10000:raise ValueError('單次建立專案最多匯入 10000 張照片，請分批整理。')
    return photos

def start_folder_import(store,source,photos,max_upload,atomic_json):
    with store.lock:
        job={'id':secrets.token_hex(6),'kind':'import','status':'running','done':0,'total':len(photos),'errors':[],'outputs':[],'duplicates':0}
        store.jobs[job['id']]=job
    def run():
        for path in photos:
            try:
                if path.stat().st_size>max_upload:raise ValueError('單檔大小超過 512 MB。')
                r=store.import_file(path.name,path.relative_to(source).as_posix(),path.read_bytes())
                with store.lock:
                    job['outputs'].append({'id':r['item']['id'],'name':path.name,'duplicate':r['duplicate']})
                    if r['duplicate']:job['duplicates']+=1
            except Exception as e:
                with store.lock:job['errors'].append({'name':path.relative_to(source).as_posix(),'error':str(e)})
            finally:
                with store.lock:job['done']+=1
        with store.lock:
            job['status']='completed';atomic_json(store.root/'folder_import_report.json',{'source_folder':str(source),**job});store.save()
    threading.Thread(target=run,daemon=True).start()
    return job

def busy(store):return any(j.get('status') in ['queued','running'] for j in store.jobs.values())

def owned_session_remove(root,token):
    path=Path(root)/'server_session.json'
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
        if data.get('pid')==os.getpid() and data.get('url','').endswith('token='+token):path.unlink(missing_ok=True)
    except (OSError,ValueError):pass

def change_project(server,kind,store_class,app_root,supported,max_upload,atomic_json,picker=native_pick):
    # The picker is native: arbitrary paths supplied by web requests are ignored.
    current=server.store
    with current.lock:
        if busy(current):raise ValueError('請等待匯入、辨識或輸出完成後，再切換專案。')
    choice=picker(app_root,kind,current.root.parent)
    if choice.get('cancelled'):return {'cancelled':True}
    destination=Path(choice.get('workspace','')).resolve()
    photos=[];source=None
    if kind=='open':
        if not (destination/'Film_Project.json').is_file():raise ValueError('所選資料夾沒有 Film_Project.json，請使用「從照片資料夾建立專案」。')
        if destination==current.root:return {'cancelled':False,'unchanged':True}
    else:
        source=Path(choice.get('source','')).resolve()
        if not source.is_dir():raise ValueError('來源照片資料夾不存在。')
        if destination==source or destination.is_relative_to(source):raise ValueError('請將新專案存放在來源資料夾之外，避免把來源和專案副本混在一起。')
        if destination.exists():raise ValueError('新專案的位置已存在；請選擇另一個名稱，或使用「開啟專案」。')
        if not destination.parent.is_dir():raise ValueError('專案保存位置不存在。')
        photos=scan_photos(source,supported)
        if not photos:raise ValueError('來源資料夾沒有支援的 JPEG、PNG 或 TIFF 照片。')
    # Recheck after the modal dialog: another tab could have started work.
    with current.lock:
        if busy(current):raise ValueError('目前專案有工作進行中，請完成後再重試。')
        current.save()
        if kind=='new':destination.mkdir(exist_ok=False)
        new=store_class(destination)
        if kind=='new':
            new.state['name']=str(choice.get('name') or source.name or '新底片專案')[:120]
            new.state['source_folder']=str(source);new.save()
        old=server.store
        if hasattr(server,'token'):
            url=f'http://127.0.0.1:{server.server_port}/?token={server.token}'
            atomic_json(new.root/'server_session.json',{'url':url,'pid':os.getpid(),'started':time.strftime('%Y-%m-%dT%H:%M:%S')})
        server.store=new
        if hasattr(server,'token'):owned_session_remove(old.root,server.token)
        try:atomic_json(Path(app_root)/'last_project.json',{'workspace':str(new.root)})
        except OSError:pass
        old.queue.put(None)
        job=start_folder_import(new,source,photos,max_upload,atomic_json) if kind=='new' else None
    return {'cancelled':False,'project_name':new.state['name'],'workspace':str(new.root),'import_job':job.copy() if job else None}
