"""Full-depth image IO and single-pass geometry for the local review app."""
from __future__ import annotations
import io, math, struct, zlib, xml.etree.ElementTree as ET
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from film_border_core import crop, inset_quad

SUPPORTED={'.jpg','.jpeg','.png','.tif','.tiff'}

def load_source(path):
    path=Path(path)
    with Image.open(path) as p:
        if getattr(p,'n_frames',1)!=1: raise ValueError('暫不支援多頁 TIFF，請先拆成單張。')
        ex=p.getexif(); orientation=int(ex.get(274,1)); dpi=p.info.get('dpi')
        try: exif=ex.tobytes() if ex else b''
        except Exception: exif=b''
        meta={'exif':exif,'icc':p.info.get('icc_profile',b''),'dpi':list(dpi) if dpi and all(math.isfinite(float(x)) and float(x)>0 for x in dpi[:2]) else None,
              'xmp':p.info.get('xmp') or p.info.get('XML:com.adobe.xmp') or (p.tag_v2.get(700,b'') if hasattr(p,'tag_v2') else b''),'orientation_original':orientation,'source_format':p.format}
    im=cv2.imdecode(np.fromfile(str(path),np.uint8),cv2.IMREAD_UNCHANGED)
    if im is None: raise ValueError('無法讀取這個影像格式。')
    if im.dtype not in (np.uint8,np.uint16):raise ValueError('只支援 8-bit／16-bit 整數影像。')
    if im.ndim==3 and im.shape[2] not in (3,4):raise ValueError('不支援這種影像通道數。')
    if orientation==2:im=cv2.flip(im,1)
    elif orientation==3:im=cv2.rotate(im,cv2.ROTATE_180)
    elif orientation==4:im=cv2.flip(im,0)
    elif orientation==5:im=cv2.transpose(im)
    elif orientation==6:im=cv2.rotate(im,cv2.ROTATE_90_CLOCKWISE)
    elif orientation==7:im=cv2.flip(cv2.transpose(im),-1)
    elif orientation==8:im=cv2.rotate(im,cv2.ROTATE_90_COUNTERCLOCKWISE)
    return im,meta

def display_image(im,max_edge=1400):
    if im.dtype==np.uint16:
        lo,hi=np.percentile(im,[.2,99.8]); im=np.clip((im.astype(np.float32)-lo)*255/max(hi-lo,1),0,255).astype(np.uint8)
    if max(im.shape[:2])>max_edge:
        s=max_edge/max(im.shape[:2]);im=cv2.resize(im,(round(im.shape[1]*s),round(im.shape[0]*s)),interpolation=cv2.INTER_AREA)
    return im

def jpeg_bytes(im,max_edge=1400):
    view=display_image(im,max_edge)
    if view.ndim==3 and view.shape[2]==4:view=cv2.cvtColor(view,cv2.COLOR_BGRA2BGR)
    ok,b=cv2.imencode('.jpg',view,[cv2.IMWRITE_JPEG_QUALITY,88])
    if not ok:raise ValueError('預覽編碼失敗。')
    return b.tobytes()

def validate_quad(q,w,h):
    q=np.asarray(q,np.float32)
    if q.shape!=(4,2) or not np.isfinite(q).all():raise ValueError('需要四個有效角點。')
    if np.any(q[:,0]<0) or np.any(q[:,0]>=w) or np.any(q[:,1]<0) or np.any(q[:,1]>=h):raise ValueError('裁切框超出原圖範圍，請調整角點。')
    if not cv2.isContourConvex(q.reshape(-1,1,2)) or abs(cv2.contourArea(q))<16:raise ValueError('四角需依左上、右上、右下、左下排列，且不可交叉。')
    return q

def effective_quad(item):
    w,h=item['width'],item['height'];q=validate_quad(item['corners'],w,h)
    if item.get('mode','perspective')=='rotate':
        center=q.mean(0);angle=math.atan2(q[1,1]-q[0,1],q[1,0]-q[0,0]);u=np.array([math.cos(angle),math.sin(angle)])
        v=np.array([-u[1],u[0]]);ww=(np.linalg.norm(q[1]-q[0])+np.linalg.norm(q[2]-q[3]))/2;hh=(np.linalg.norm(q[3]-q[0])+np.linalg.norm(q[2]-q[1]))/2
        q=np.array([center-u*ww/2-v*hh/2,center+u*ww/2-v*hh/2,center+u*ww/2+v*hh/2,center-u*ww/2+v*hh/2],np.float32)
    angle=float(item.get('rotation_deg',0))
    if not math.isfinite(angle) or abs(angle)>45:raise ValueError('旋轉量需介於 −45° 與 45°。')
    if angle:
        rad=math.radians(angle);m=np.array([[math.cos(rad),-math.sin(rad)],[math.sin(rad),math.cos(rad)]],np.float32);center=q.mean(0);q=(q-center)@m.T+center
    inset=float(item.get('inset',0))
    if not math.isfinite(inset) or not 0<=inset<=10000:raise ValueError('內縮量無效。')
    if item.get('inset_unit','px')=='percent':inset=inset/100*min(w,h)
    if inset>0:
        lengths=[np.linalg.norm(q[(i+1)%4]-q[i]) for i in range(4)]
        if inset>=min(lengths)/3:raise ValueError('內縮量過大。')
        q=inset_quad(q,inset)
    return validate_quad(q,w,h)

def crop_item(im,item):return crop(im,effective_quad(item).tolist())

def export_exif(meta,w,h):
    ex=Image.Exif()
    try:
        if meta.get('exif'):ex.load(meta['exif'])
    except Exception:ex=Image.Exif()
    # Offsets, thumbnails, old strip geometry and orientation no longer apply.
    for tag in [258,259,262,273,277,278,279,284,288,289,317,324,325,330,338,339,513,514,50719,50720,50829]:
        if tag in ex:del ex[tag]
    ex[274]=1;ex[256]=w;ex[257]=h;ex[305]='Film Border Cropper V1 preview'
    try:
        sub=ex.get_ifd(34665);sub[40962]=w;sub[40963]=h
        # MakerNote/private offsets are not safely relocatable.
        sub.pop(37500,None)
        if sub:ex[34665]=sub
    except Exception:pass
    return ex

def clean_xmp(meta,w,h):
    xmp=meta.get('xmp',b'')
    if isinstance(xmp,str):xmp=xmp.encode('utf-8')
    if not xmp:return b''
    try:
        tree=ET.fromstring(xmp)
        values={'ImageWidth':str(w),'ImageLength':str(h),'PixelXDimension':str(w),'PixelYDimension':str(h),'Orientation':'1'}
        for node in tree.iter():
            for child in list(node):
                if 'camera-raw-settings' in child.tag:node.remove(child)
            for k in list(node.attrib):
                if 'camera-raw-settings' in k:
                    del node.attrib[k];continue
                name=k.split('}')[-1]
                if name in values:node.set(k,values[name])
            name=node.tag.split('}')[-1]
            if name in values:node.text=values[name]
        return ET.tostring(tree,encoding='utf-8')
    except Exception:return b''

def png_chunk(tag,data):return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)

def encode_png(im,meta):
    ok,b=cv2.imencode('.png',im,[cv2.IMWRITE_PNG_COMPRESSION,4])
    if not ok:raise ValueError('PNG 輸出失敗。')
    base=b.tobytes();pos=8;chunks=[]
    while pos<len(base):
        n=struct.unpack('>I',base[pos:pos+4])[0];tag=base[pos+4:pos+8];chunks.append((tag,base[pos:pos+n+12]));pos+=n+12
    extra=[]
    if meta.get('icc'):extra.append(png_chunk(b'iCCP',b'ICC Profile\0\0'+zlib.compress(meta['icc'])))
    dpi=meta.get('dpi')
    if dpi:extra.append(png_chunk(b'pHYs',struct.pack('>IIB',round(dpi[0]/.0254),round(dpi[1]/.0254),1)))
    exif=export_exif(meta,im.shape[1],im.shape[0]).tobytes()
    if exif:extra.append(png_chunk(b'eXIf',exif[6:] if exif.startswith(b'Exif\0\0') else exif))
    xmp=clean_xmp(meta,im.shape[1],im.shape[0])
    if xmp:extra.append(png_chunk(b'iTXt',b'XML:com.adobe.xmp\0\0\0\0\0'+xmp))
    return base[:8]+b''.join(part+(b''.join(extra) if tag==b'IHDR' else b'') for tag,part in chunks)

# TIFF encoder preserves RGB/grayscale uint16 without converting through Pillow.
# Standard metadata is explicit; private MakerNote/offset tags are omitted.
def encode_tiff(im,meta,compress=True):
    if im.ndim==3:
        order=[2,1,0,3] if im.shape[2]==4 else [2,1,0];rgb=im[:,:,order]
    else:rgb=im
    h,w=rgb.shape[:2];channels=rgb.shape[2] if rgb.ndim==3 else 1;bits=16 if rgb.dtype==np.uint16 else 8
    pixels=np.ascontiguousarray(rgb.astype('<u2') if bits==16 else rgb).tobytes();payload=zlib.compress(pixels,6) if compress else pixels
    entries={256:(4,[w]),257:(4,[h]),258:(3,[bits]*channels),259:(3,[8 if compress else 1]),262:(3,[2 if channels>=3 else 1]),273:(4,[0]),274:(3,[1]),277:(3,[channels]),278:(4,[h]),279:(4,[len(payload)]),284:(3,[1]),305:(2,'Film Border Cropper V1 preview')}
    if channels==4:entries[338]=(3,[2])
    dpi=meta.get('dpi')
    if dpi:entries.update({282:(5,[(round(dpi[0]*1000),1000)]),283:(5,[(round(dpi[1]*1000),1000)]),296:(3,[2])})
    if meta.get('icc'):entries[34675]=(7,meta['icc'])
    xmp=clean_xmp(meta,w,h)
    if xmp:entries[700]=(1,xmp)
    ex=export_exif(meta,w,h)
    for tag in [270,271,272,306,315,33432]:
        if isinstance(ex.get(tag),str):entries[tag]=(2,ex.get(tag))
    sub={40962:(4,[w]),40963:(4,[h])}
    try:source_sub=ex.get_ifd(34665)
    except Exception:source_sub={}
    for tag in [33434,33437,34850,34855,36867,36868,37377,37378,37380,37383,37385,37386,41985,41986,41987,41988,41989,41990]:
        v=source_sub.get(tag)
        if v is None:continue
        try:
            if isinstance(v,str):sub[tag]=(2,v)
            elif tag in [33434,33437,37378,37386,41988]:sub[tag]=(5,[(round(float(v)*1000000),1000000)])
            elif tag in [37377,37380]:sub[tag]=(10,[(round(float(v)*1000000),1000000)])
            else:sub[tag]=(3,[int(v)])
        except Exception:pass
    entries[34665]=(4,[0])
    gps={}
    gps_types={0:1,1:2,2:5,3:2,4:5,5:1,6:5,7:5,8:2,9:2,10:2,11:5,12:2,13:5,14:2,15:5,16:2,17:5,18:2,19:5,20:2,21:5,22:2,23:5,24:5,25:2,26:7,27:7,28:7,29:2,30:3,31:5}
    try:source_gps=ex.get_ifd(34853)
    except Exception:source_gps={}
    for tag,value in source_gps.items():
        typ=gps_types.get(tag)
        if not typ:continue
        try:
            if typ==2:v=str(value)
            elif typ in (1,7):v=bytes(value) if not isinstance(value,int) else bytes([value])
            elif typ==3:v=[int(value)]
            else:
                vals=value if isinstance(value,(tuple,list)) else [value]
                v=[(round(float(x)*1000000),1000000) for x in vals]
            gps[tag]=(typ,v)
        except Exception:pass
    if gps:entries[34853]=(4,[0])
    def rawvalue(typ,value):
        if typ==2:
            b=str(value).encode('utf-8')+b'\0';return b,len(b)
        if typ in [1,7]:return bytes(value),len(value)
        if typ in [3,4]:return struct.pack('<'+('H' if typ==3 else 'I')*len(value),*value),len(value)
        flat=[x for pair in value for x in pair];return struct.pack('<'+('I' if typ==5 else 'i')*len(flat),*flat),len(value)
    def ifd(mapping,offset):
        area=bytearray();block=bytearray(struct.pack('<H',len(mapping)));base=offset+2+12*len(mapping)+4
        for tag,(typ,val) in sorted(mapping.items()):
            b,n=rawvalue(typ,val)
            if len(b)<=4:v=b.ljust(4,b'\0')
            else:
                if len(area)%2:area+=b'\0'
                v=struct.pack('<I',base+len(area));area+=b
            block+=struct.pack('<HHI',tag,typ,n)+v
        return block+struct.pack('<I',0)+area
    # Sizes are invariant when the two offset placeholders are filled.
    first=ifd(entries,8);suboff=8+len(first);suboff+=(suboff%2);subbytes=ifd(sub,suboff);gpsoff=suboff+len(subbytes);gpsoff+=(gpsoff%2);gpsbytes=ifd(gps,gpsoff) if gps else b'';pixeloff=gpsoff+len(gpsbytes);pixeloff+=(pixeloff%2)
    entries[34665]=(4,[suboff]);entries[273]=(4,[pixeloff])
    if gps:entries[34853]=(4,[gpsoff])
    first=ifd(entries,8)
    return b'II*\0'+struct.pack('<I',8)+first+b'\0'*(suboff-8-len(first))+subbytes+b'\0'*(gpsoff-suboff-len(subbytes))+gpsbytes+b'\0'*(pixeloff-gpsoff-len(gpsbytes))+payload

def encode_output(im,meta,settings):
    fmt=settings.get('format','tiff').lower();quality=int(settings.get('quality',95));depth=settings.get('bit_depth','preserve')
    if not 1<=quality<=100:raise ValueError('JPEG 品質需為 1–100。')
    if depth=='8' and im.dtype==np.uint16:im=((im.astype(np.uint32)+128)//257).astype(np.uint8)
    warnings=[]
    if fmt in ['jpg','jpeg']:
        if im.dtype==np.uint16:raise ValueError('JPEG 不支援 16-bit；請選 TIFF／PNG，或明確選擇轉 8-bit。')
        if im.ndim==3 and im.shape[2]==4:raise ValueError('JPEG 不支援透明通道；請使用 PNG／TIFF。')
        rgb=cv2.cvtColor(im,cv2.COLOR_BGR2RGB) if im.ndim==3 else im;p=Image.fromarray(rgb);buf=io.BytesIO();kw={'quality':quality,'subsampling':0,'exif':export_exif(meta,im.shape[1],im.shape[0]).tobytes()}
        if meta.get('icc'):kw['icc_profile']=meta['icc']
        if meta.get('dpi'):kw['dpi']=tuple(meta['dpi'])
        xmp=clean_xmp(meta,im.shape[1],im.shape[0])
        if xmp:kw['xmp']=xmp
        p.save(buf,format='JPEG',**kw);data=buf.getvalue();ext='.jpg'
    elif fmt=='png':data=encode_png(im,meta);ext='.png'
    elif fmt in ['tiff','tif']:data=encode_tiff(im,meta,settings.get('tiff_compress',True));ext='.tif';warnings.append('TIFF 保留標準拍攝與 GPS 欄位；私有 MakerNote 等未完整轉存，原檔仍保留。')
    else:raise ValueError('不支援的輸出格式。')
    if meta.get('exif'):warnings.append('縮圖與私有位移型中繼資料不轉存；方向與尺寸已更新。')
    return data,ext,warnings
