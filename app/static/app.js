'use strict';
const $=id=>document.getElementById(id), token=window.FILM_TOKEN;
let state={project:{items:[],settings:{}},jobs:[]},current=null,selected=new Set(),image=null,imageId=null,zoom=1,pan={x:0,y:0},drag=null,cornerIndex=0,compare=false,importing=false,editing=false,polling=false,lastCropKey='',lastListHTML='',saveChain=Promise.resolve(),reviewBusy=false,refreshInFlight=null,exportPending=false,projectBusy=false,removeBusy=false;
const labels={high:'高信心',medium:'中信心',low:'低信心',rejected:'需手動處理',pending:'待辨識'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const imageUrl=(id,kind='original')=>`/api/image/${id}/${kind}?token=${encodeURIComponent(token)}`;
function notify(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(notify.timer);notify.timer=setTimeout(()=>$('toast').hidden=true,4500);}
async function api(path,body,raw=false){const opt={headers:{'X-Film-Token':token}};if(state.project.project_id)opt.headers['X-Film-Project']=state.project.project_id;if(body!==undefined){opt.method='POST';opt.body=raw?body:JSON.stringify(body);opt.headers['Content-Type']=raw?'application/octet-stream':'application/json';}const response=await fetch(path,opt);const result=await response.json();if(!response.ok)throw Error(result.error||'操作未完成。');return result;}
function item(){return state.project.items.find(r=>r.id===current);}
function needsReview(r){return !r.confirmed&&!r.auto_pass&&r.review_decision!=='abstention_appropriate';}
function filtered(){const f=$('filter').value;return state.project.items.filter(r=>f==='all'||f==='confirmed'&&r.confirmed||f==='review'&&needsReview(r)||f==='rejected'&&['rejected','missing','error'].includes(r.status)||r.confidence===f);}
function eligible(){const ids=selected.size?selected:null;return state.project.items.filter(r=>(!ids||ids.has(r.id))&&(r.confirmed||r.auto_pass)&&r.corners&&!['missing','rejected','error'].includes(r.status));}
async function refresh(force=false){
 while(refreshInFlight){if(!force){await refreshInFlight;return;}await refreshInFlight;}
 polling=true;const work=(async()=>{try{const next=await api('/api/state');if(!editing||force){state=next;if(!current&&state.project.items.length){const visible=filtered();if(visible.length)current=visible[0].id;}render();}}catch(e){if(force)notify(e.message);}})();refreshInFlight=work;
 try{await work;}finally{if(refreshInFlight===work){refreshInFlight=null;polling=false;}}
}
function render(){
 const items=state.project.items;const validIds=new Set(items.map(r=>r.id));selected.forEach(id=>{if(!validIds.has(id))selected.delete(id);});if(current&&!validIds.has(current)){current=filtered()[0]?.id||null;imageId=null;image=null;lastCropKey='';}
 const removeBlocked=removeBusy||projectBusy||importing||reviewBusy||editing||exportPending||state.jobs.some(j=>['queued','running'].includes(j.status));$('removeSelected').disabled=removeBlocked||!selected.size;$('removeCurrent').disabled=removeBlocked||!item();$('projectPath').textContent=state.storage?.project_root||'';$('projectPath').title=state.storage?.project_root||'目前專案保存位置';$('count').textContent=items.length;if(document.activeElement!==$('projectName'))$('projectName').value=state.project.name;
 $('stats').innerHTML=`<div class="stat"><strong>${items.filter(r=>r.confidence==='high').length}</strong><span>高信心</span></div><div class="stat"><strong>${items.filter(needsReview).length}</strong><span>待檢查</span></div><div class="stat"><strong>${items.filter(r=>r.confirmed).length}</strong><span>人工已確認</span></div>`;
 const list=filtered();const nextListHTML=list.length?list.map(r=>`<div class="photo-card ${r.id===current?'active':''}" data-id="${r.id}" role="button" tabindex="0" aria-label="${esc(r.name)}"><input type="checkbox" aria-label="選取 ${esc(r.name)}" data-select="${r.id}" ${selected.has(r.id)?'checked':''}><img src="${imageUrl(r.id)}" alt="" loading="lazy"><div class="photo-text"><strong title="${esc(r.name)}">${esc(r.name)}</strong><span class="badge ${r.confirmed?'confirmed':r.confidence}">${r.confirmed?'✓ 人工已確認':labels[r.confidence]||'處理中'}</span><div class="detail">${r.width} × ${r.height} · ${r.bit_depth}-bit${r.status==='detecting'?' · 辨識中':''}</div></div></div>`).join(''):'<p class="hint" style="padding:15px">沒有符合篩選條件的照片。</p>';if(nextListHTML!==lastListHTML){$('photoList').innerHTML=nextListHTML;lastListHTML=nextListHTML;}
 $('selectedCount').textContent=selected.size?`已選取 ${selected.size} 張`:'未選取照片';$('exportCount').textContent=`${eligible().length} 張可輸出${selected.size?'（選取範圍）':''}`;$('export').disabled=!eligible().length;
 const settings=state.project.settings||{};for(const [id,k]of[['format','format'],['bitDepth','bit_depth'],['quality','quality']])if(document.activeElement!==$(id)&&settings[k]!==undefined)$(id).value=settings[k];
 renderEditor();renderJobs();
}
function renderEditor(){
 const r=item();$('empty').hidden=!!r;$('editor').hidden=!r;
 if(!r){const hasPhotos=state.project.items.length>0;$('emptyTitle').textContent=hasPhotos?'這個篩選已沒有待處理照片':'從一卷底片開始';$('emptyMessage').textContent=state.jobs.some(j=>j.kind==='import'&&j.status!=='completed')?'正在從選定的資料夾匯入照片，辨識會在背景繼續。':hasPhotos?'可切換為全部照片，繼續查看已確認或拒絕的結果。':'匯入照片或資料夾，開始檢查候選邊界。';$('emptyImport').hidden=hasPhotos;return;}
 $('photoName').textContent=r.name;$('photoInfo').textContent=`${r.width} × ${r.height} px · ${r.bit_depth}-bit · ${r.channels===1?'灰階':'彩色通道'}${r.metadata?.icc?' · ICC':''}${r.metadata?.dpi?' · '+r.metadata.dpi.map(x=>Math.round(x)).join(' × ')+' DPI':''}`;
 const list=filtered();$('position').textContent=`${Math.max(0,list.findIndex(x=>x.id===r.id)+1)} / ${list.length}`;
 $('confidence').className=`badge ${r.confidence}`;$('confidence').textContent=labels[r.confidence]||'處理中';$('reviewState').textContent=r.confirmed?'✓ 人工已確認':r.review_decision==='abstention_appropriate'?'✓ 已保留拒絕':r.auto_pass?'通過自動門檻':'等待你確認';
 if(!editing){for(const [id,key]of[['inset','inset'],['insetUnit','inset_unit'],['rotation','rotation_deg']])if(document.activeElement!==$(id))$(id).value=r[key]??0;$('perspective').checked=r.mode==='perspective';}
 $('manualFrame').hidden=!!r.corners;$('reject').disabled=reviewBusy||removeBusy;$('accept').disabled=reviewBusy||removeBusy||!r.corners||['detecting','queued','missing'].includes(r.status);$('reset').disabled=!r.detection;
 $('accept').textContent=r.confirmed?'✓ 已確認，下一張':'✓ 確認這張邊界　Enter';
 $('imageMessage').textContent=r.status==='missing'?'來源照片遺失或校驗不符，請恢復 Originals 中的原檔。':r.status==='detecting'||r.status==='queued'?'正在分析照片，請稍候…':!r.corners?'這張邊界不夠清楚。可以保留拒絕，或建立手動框。':r.confirmed?'這個邊界已確認。修改角點或參數後，需要重新確認。':r.auto_pass?'候選框通過自動門檻；你仍可調整、人工確認。':'請檢查四邊，避免殘留片邊或裁入畫面。';
 if(imageId!==r.id){imageId=r.id;image=null;zoom=1;pan={x:0,y:0};const im=new Image();const expected=r.id;im.onload=()=>{if(current===expected){image=im;draw();}};im.src=imageUrl(r.id);}
 draw();if(compare)loadCrop();
}
function view(){const r=item(),box=$('canvasWrap').getBoundingClientRect();if(!r)return null;const scale=Math.min((box.width-26)/r.width,(box.height-26)/r.height)*zoom;return{width:box.width,height:box.height,scale,x:(box.width-r.width*scale)/2+pan.x,y:(box.height-r.height*scale)/2+pan.y};}
function insetQuad(q,margin){let center=q.reduce((a,p)=>[a[0]+p[0]/4,a[1]+p[1]/4],[0,0]),lines=[];for(let i=0;i<4;i++){let p=q[i],next=q[(i+1)%4],v=[next[0]-p[0],next[1]-p[1]],n=[-v[1],v[0]],len=Math.hypot(...n);n=n.map(x=>x/len);if((center[0]-p[0])*n[0]+(center[1]-p[1])*n[1]<0)n=n.map(x=>-x);lines.push({p:[p[0]+margin*n[0],p[1]+margin*n[1]],v});}return lines.map((line,i)=>{let a=lines[(i+3)%4],b=line,den=a.v[0]*b.v[1]-a.v[1]*b.v[0],d=[b.p[0]-a.p[0],b.p[1]-a.p[1]],t=(d[0]*b.v[1]-d[1]*b.v[0])/den;return[a.p[0]+t*a.v[0],a.p[1]+t*a.v[1]];});}
function appliedQuad(r){if(!r.corners)return null;let q=r.corners.map(p=>[...p]),center=q.reduce((a,p)=>[a[0]+p[0]/4,a[1]+p[1]/4],[0,0]);if(r.mode==='rotate'){let angle=Math.atan2(q[1][1]-q[0][1],q[1][0]-q[0][0]),u=[Math.cos(angle),Math.sin(angle)],v=[-u[1],u[0]],dist=(a,b)=>Math.hypot(a[0]-b[0],a[1]-b[1]),w=(dist(q[1],q[0])+dist(q[2],q[3]))/2,h=(dist(q[3],q[0])+dist(q[2],q[1]))/2;q=[[-1,-1],[1,-1],[1,1],[-1,1]].map(([x,y])=>[center[0]+x*w*u[0]/2+y*h*v[0]/2,center[1]+x*w*u[1]/2+y*h*v[1]/2]);}if(r.rotation_deg){let a=r.rotation_deg*Math.PI/180;q=q.map(([x,y])=>[center[0]+(x-center[0])*Math.cos(a)-(y-center[1])*Math.sin(a),center[1]+(x-center[0])*Math.sin(a)+(y-center[1])*Math.cos(a)]);}const margin=r.inset_unit==='percent'?r.inset/100*Math.min(r.width,r.height):r.inset;if(margin>0)q=insetQuad(q,margin);return q;}
function draw(){const canvas=$('canvas'),r=item(),v=view();if(!r||!v)return;const ratio=window.devicePixelRatio||1;canvas.width=Math.round(v.width*ratio);canvas.height=Math.round(v.height*ratio);const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);ctx.fillStyle='#1b2422';ctx.fillRect(0,0,v.width,v.height);if(image)ctx.drawImage(image,v.x,v.y,r.width*v.scale,r.height*v.scale);if(r.corners){const points=r.corners.map(([x,y])=>[v.x+x*v.scale,v.y+y*v.scale]);ctx.strokeStyle=r.confirmed?'#6fff9b':'#f6ce6c';ctx.lineWidth=2;ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.closePath();ctx.stroke();points.forEach(([x,y],i)=>{ctx.fillStyle=i===cornerIndex?'#fff':'#f6ce6c';ctx.strokeStyle='#1b2422';ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,7,0,Math.PI*2);ctx.fill();ctx.stroke();ctx.fillStyle='#fff';ctx.font='11px sans-serif';ctx.fillText(String(i+1),x+11,y-9);});if(r.inset||r.rotation_deg||r.mode==='rotate'){const applied=appliedQuad(r);ctx.strokeStyle='#63ddd4';ctx.setLineDash([5,4]);ctx.beginPath();applied.forEach(([x,y],i)=>i?ctx.lineTo(v.x+x*v.scale,v.y+y*v.scale):ctx.moveTo(v.x+x*v.scale,v.y+y*v.scale));ctx.closePath();ctx.stroke();ctx.setLineDash([]);}}
 $('zoomLabel').textContent=zoom===1?'適合畫面':Math.round(zoom*100)+'%';}
function point(event){const b=$('canvas').getBoundingClientRect();return{x:event.clientX-b.left,y:event.clientY-b.top};}
$('canvas').addEventListener('pointerdown',e=>{const r=item(),v=view();if(!r||!v)return;const p=point(e);if(e.altKey){drag={type:'pan',p,pan:{...pan}};}else if(r.corners){let best=-1,dist=22;r.corners.forEach(([x,y],i)=>{const d=Math.hypot(v.x+x*v.scale-p.x,v.y+y*v.scale-p.y);if(d<dist){best=i;dist=d;}});if(best>=0){cornerIndex=best;drag={type:'corner',original:r.corners.map(x=>[...x])};editing=true;}}if(drag){$('canvas').setPointerCapture(e.pointerId);draw();}});
$('canvas').addEventListener('pointermove',e=>{if(!drag)return;const p=point(e),r=item(),v=view();if(drag.type==='pan'){pan={x:drag.pan.x+p.x-drag.p.x,y:drag.pan.y+p.y-drag.p.y};}else{r.corners[cornerIndex]=[Math.max(0,Math.min(r.width-1,(p.x-v.x)/v.scale)),Math.max(0,Math.min(r.height-1,(p.y-v.y)/v.scale))];}draw();});
async function finishDrag(){if(!drag)return;const type=drag.type;drag=null;if(type==='corner'){const q=item().corners.map(p=>[...p]);editing=false;await saveEdit({corners:q});}}
$('canvas').addEventListener('pointerup',finishDrag);$('canvas').addEventListener('pointercancel',finishDrag);$('canvas').addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.5,Math.min(8,zoom*(e.deltaY<0?1.12:1/1.12)));draw();},{passive:false});
async function saveEdit(patch){if(projectBusy||removeBusy){notify('請先完成專案選擇。');return false;}const id=current;$('saveStatus').textContent='保存中…';saveChain=saveChain.then(async()=>{try{await api('/api/item/'+id,patch);$('saveStatus').textContent='已自動保存';lastCropKey='';await refresh(true);return true;}catch(e){notify(e.message);$('saveStatus').textContent='未保存，請檢查';await refresh(true);return false;}});return await saveChain;}
function navigate(step){const list=filtered();if(!list.length)return;const index=list.findIndex(r=>r.id===current);selectPhoto(list[(index+step+list.length)%list.length].id);}
function selectPhoto(id){if(editing)return;current=id;lastCropKey='';render();}
$('photoList').addEventListener('click',e=>{const checkbox=e.target.closest('[data-select]');if(checkbox){checkbox.checked?selected.add(checkbox.dataset.select):selected.delete(checkbox.dataset.select);render();return;}const card=e.target.closest('[data-id]');if(card)selectPhoto(card.dataset.id);});
$('photoList').addEventListener('keydown',e=>{if(e.key==='Enter'){const card=e.target.closest('[data-id]');if(card)selectPhoto(card.dataset.id);}});
$('filter').onchange=()=>{const list=filtered();if(!list.some(r=>r.id===current)&&list.length)current=list[0].id;render();};$('selectAll').onclick=()=>{const list=filtered();const all=list.length&&list.every(r=>selected.has(r.id));list.forEach(r=>all?selected.delete(r.id):selected.add(r.id));render();};
async function removePhotos(ids){
 if(removeBusy||projectBusy||importing||reviewBusy||editing||exportPending||state.jobs.some(j=>['queued','running'].includes(j.status))){notify('請等待匯入、辨識、編輯或輸出完成後，再移除照片。');return;}
 ids=[...new Set(ids)].filter(id=>state.project.items.some(r=>r.id===id));if(!ids.length)return;
 if(!window.confirm(`從專案移除 ${ids.length} 張照片？\n照片副本、預覽和編輯紀錄會移至專案內的 Removed 備份資料夾。\n匯入來源與已輸出的檔案不受影響。`))return;
 removeBusy=true;render();
 try{
  await saveChain;await api('/api/items/remove',{ids});
  ids.forEach(id=>selected.delete(id));if(ids.includes(current)){current=null;imageId=null;image=null;lastCropKey='';const previous=$('cropImage').dataset.blob;if(previous)URL.revokeObjectURL(previous);delete $('cropImage').dataset.blob;$('cropImage').hidden=true;}
  await refresh(true);notify(`已從專案移除 ${ids.length} 張。備份保留在專案的 Removed 資料夾。`);
 }catch(e){notify(e.message);await refresh(true);}finally{removeBusy=false;render();}
}
$('removeSelected').onclick=()=>removePhotos([...selected]);
$('removeCurrent').onclick=()=>removePhotos(current?[current]:[]);
$('prevPhoto').onclick=()=>navigate(-1);$('nextPhoto').onclick=()=>navigate(1);
for(const [id,key]of[['inset','inset'],['rotation','rotation_deg']])$(id).onchange=()=>saveEdit({[key]:Number($(id).value)});
$('insetUnit').onchange=()=>saveEdit({inset_unit:$('insetUnit').value});$('perspective').onchange=()=>saveEdit({mode:$('perspective').checked?'perspective':'rotate'});
$('reset').onclick=()=>saveEdit({reset:true});$('manualFrame').onclick=()=>{const r=item(),w=r.width,h=r.height;saveEdit({corners:[[w*.03,h*.03],[w*.97,h*.03],[w*.97,h*.97],[w*.03,h*.97]]});};
function advanceReviewed(id,before,message){
 if(current!==id){notify(message);return;}
 const visible=filtered(),allowed=new Set(visible.map(r=>r.id));const next=before.slice(before.indexOf(id)+1).find(x=>allowed.has(x));
 if(next){selectPhoto(next);notify(message);return;}
 if(allowed.has(id)){notify(message+' 已到最後一張。');return;}
 if(visible.length){selectPhoto(visible[0].id);notify(message);return;}
 current=null;imageId=null;render();notify(message+' 目前篩選已全部處理。');
}
async function reviewAndAdvance(patch,message){
 if(reviewBusy||removeBusy||!item())return;const id=current,before=filtered().map(r=>r.id);reviewBusy=true;renderEditor();
 try{if(await saveEdit(patch))advanceReviewed(id,before,message);}finally{reviewBusy=false;renderEditor();}
}
$('reject').onclick=()=>reviewAndAdvance({rejected:true},'已保留拒絕，不會輸出這張。');
$('accept').onclick=()=>{const r=item();if(!r||!r.corners)return;return reviewAndAdvance({confirmed:true},'這張邊界已確認。');};
$('redetect').onclick=async()=>{if(!current)return;try{await api('/api/detect',{ids:[current]});notify('已排入辨識，原手動設定會以新結果取代。');await refresh(true);}catch(e){notify(e.message);}};
$('applyBatch').onclick=async()=>{if(!selected.size){notify('請先勾選要套用的照片。');return;}try{const r=await api('/api/batch',{ids:[...selected],patch:{inset:Number($('batchInset').value),inset_unit:$('batchUnit').value}});const errors=r.results.filter(x=>!x.ok);notify(`已套用 ${r.results.length-errors.length} 張${errors.length?'；'+errors.length+' 張未套用，請先建立有效框':''}。修改後需重新確認。`);lastCropKey='';await refresh(true);}catch(e){notify(e.message);}};
$('zoomIn').onclick=()=>{zoom=Math.min(8,zoom*1.3);draw();};$('zoomOut').onclick=()=>{zoom=Math.max(.5,zoom/1.3);draw();};$('fit').onclick=()=>{zoom=1;pan={x:0,y:0};draw();};
function setCompare(){compare=!compare;$('compare').setAttribute('aria-pressed',String(compare));$('cropPane').hidden=!compare;$('viewLayout').classList.toggle('comparing',compare);lastCropKey='';requestAnimationFrame(draw);if(compare)loadCrop();}
$('compare').onclick=setCompare;
async function loadCrop(){const r=item();if(!r||!compare)return;if(!r.corners){$('cropImage').hidden=true;$('cropMessage').textContent='先建立裁切框，再查看預覽。';return;}const key=r.id+JSON.stringify([r.corners,r.inset,r.inset_unit,r.rotation_deg,r.mode]);if(lastCropKey===key)return;lastCropKey=key;$('cropMessage').textContent='正在產生預覽…';const id=r.id;try{const response=await fetch(imageUrl(id,'crop')+'&rev='+Date.now());if(!response.ok){const message=await response.json();throw Error(message.error);}const blob=await response.blob();if(current!==id||lastCropKey!==key)return;const previous=$('cropImage').dataset.blob;const url=URL.createObjectURL(blob);$('cropImage').src=url;$('cropImage').dataset.blob=url;$('cropImage').hidden=false;$('cropMessage').textContent='';if(previous)URL.revokeObjectURL(previous);}catch(e){$('cropMessage').textContent=e.message;$('cropImage').hidden=true;}}
new ResizeObserver(()=>requestAnimationFrame(draw)).observe($('canvasWrap'));
async function importFiles(files){if(importing||projectBusy||removeBusy){notify('請先完成目前匯入或專案選擇。');return;}const photos=Array.from(files).filter(f=>/\.(jpe?g|png|tiff?)$/i.test(f.name));if(!photos.length){notify('沒有找到支援的照片。');return;}importing=true;let errors=[],duplicates=0;for(let i=0;i<photos.length;i++){const file=photos[i];$('importProgress').textContent=`匯入 ${i+1} / ${photos.length} · ${file.name}`;try{const r=await api('/api/import?name='+encodeURIComponent(file.name)+'&relative='+encodeURIComponent(file.webkitRelativePath||file.name),file,true);if(r.duplicate)duplicates++;if(!current)current=r.item.id;}catch(e){errors.push(file.name+'：'+e.message);}await refresh(true);}importing=false;$('importProgress').textContent=`匯入完成${duplicates?' · '+duplicates+' 張重複照片已略過':''}${errors.length?' · '+errors.length+' 張失敗':''}`;if(errors.length)notify(errors.join('；'));else notify('照片已匯入，辨識在背景繼續。');$('files').value='';$('folder').value='';}
$('addFiles').onclick=$('emptyImport').onclick=()=>$('files').click();$('addFolder').onclick=()=>$('folder').click();$('files').onchange=e=>importFiles(e.target.files);$('folder').onchange=e=>importFiles(e.target.files);
document.addEventListener('dragover',e=>e.preventDefault());document.addEventListener('drop',e=>{e.preventDefault();if(e.dataTransfer.files.length)importFiles(e.dataTransfer.files);});
async function projectAction(kind){
 if(projectBusy||importing||reviewBusy||exportPending||removeBusy){notify('請等待目前操作完成。');return;}
 if(state.jobs.some(j=>['queued','running'].includes(j.status))){notify('請等待匯入、辨識或輸出完成後，再切換專案。');return;}
 await saveChain;projectBusy=true;$('openProject').disabled=true;$('newProject').disabled=true;
 try{
  notify(kind==='open'?'請在 Windows 視窗選擇專案資料夾。':'先選來源照片資料夾，再選新專案保存位置與名稱。');
  const result=await api(kind==='open'?'/api/project/open':'/api/project/new-from-folder',{});
  if(result.cancelled){notify('已取消，保留目前專案。');return;}
  selected.clear();current=null;imageId=null;image=null;lastListHTML='';lastCropKey='';compare=false;$('compare').setAttribute('aria-pressed','false');$('cropPane').hidden=true;$('viewLayout').classList.toggle('comparing',false);
  await refresh(true);notify(result.unchanged?'目前已開啟這個專案。':kind==='open'?'專案已開啟，編輯與確認狀態已恢復。':'新專案已建立，照片正在背景匯入。');
 }catch(e){notify(e.message);}finally{projectBusy=false;$('openProject').disabled=false;$('newProject').disabled=false;}
}
$('openProject').onclick=()=>projectAction('open');$('newProject').onclick=()=>projectAction('new');
$('projectName').onchange=async()=>{try{await api('/api/project/name',{name:$('projectName').value});await refresh(true);}catch(e){notify(e.message);}};
$('saveProject').onclick=()=>{const a=document.createElement('a');a.href='/api/project?token='+encodeURIComponent(token);a.download='Film_Project.json';a.click();};$('loadProject').onclick=()=>$('projectFile').click();$('projectFile').onchange=async e=>{try{const file=e.target.files[0];if(!file)return;await api('/api/project/load',JSON.parse(await file.text()));selected.clear();current=null;imageId=null;await refresh(true);notify('專案已載入。原專案已另存備份。');}catch(e){notify(e.message);}e.target.value='';};
function outputSettings(){return{format:$('format').value,quality:Number($('quality').value),bit_depth:$('bitDepth').value,tiff_compress:true};}
for(const id of ['format','quality','bitDepth'])$(id).onchange=async()=>{try{await api('/api/settings',outputSettings());}catch(e){notify(e.message);}};
$('export').onclick=async()=>{await saveChain;const ids=eligible().map(r=>r.id);if(!ids.length||exportPending)return;exportPending=true;updateDownloadButton();try{const settings=outputSettings();await api('/api/settings',settings);const job=await api('/api/export',{ids,settings});state.jobs.push(job);notify(`已排入 ${ids.length} 張完整解析度輸出。`);await refresh(true);}catch(e){notify(e.message);}finally{exportPending=false;updateDownloadButton();}};
function latestExport(){return state.jobs.filter(j=>j.kind==='export').at(-1);}
function updateDownloadButton(){const job=latestExport(),button=$('downloadExport');const ready=!exportPending&&!!(job&&job.status==='completed'&&job.download&&job.outputs?.length);button.disabled=!ready;button.textContent=exportPending||(job&&['queued','running'].includes(job.status))?'輸出中…':ready?`保存／下載 ZIP（${job.outputs.length} 張）`:'保存／下載 ZIP';button.title=ready?'下載最新一次成功輸出的 ZIP':job?.status==='completed'?'這次沒有成功輸出的照片':'輸出完成後即可保存／下載';}
$('downloadExport').onclick=()=>{const job=latestExport();if(!job||job.status!=='completed'||!job.download||!job.outputs?.length)return;const a=document.createElement('a');a.href='/api/download/'+job.id+'?token='+encodeURIComponent(token);a.download='Film_Cropped.zip';a.click();};
function renderJobs(){updateDownloadButton();const jobs=state.jobs.filter(j=>j.kind==='export'||j.kind==='import'||j.status!=='completed').slice(-5);$('jobs').innerHTML=jobs.map(j=>`<div class="job"><span>${j.kind==='export'?'批次輸出':j.kind==='import'?'資料夾匯入':'邊界辨識'} · ${j.done} / ${j.total} ${j.status==='completed'?'完成':''}</span><progress max="${Math.max(j.total,1)}" value="${j.done}"></progress>${j.download?`<a href="/api/download/${j.id}?token=${encodeURIComponent(token)}">下載輸出 ZIP（${j.outputs.length} 張）</a>`:''}${j.errors.length?`<details><summary>${j.errors.length} 張未完成</summary>${j.errors.map(e=>`<p>${esc(e.name)}：${esc(e.error)}</p>`).join('')}</details>`:''}</div>`).join('');}
$('closeApp').onclick=async()=>{try{await api('/api/shutdown',{});document.body.innerHTML='<main class="empty"><h1>專案已保存，程式已關閉。</h1><p>下次雙擊 FilmBorderCropper.exe，即可繼續。</p></main>';}catch(e){notify(e.message);}};
document.addEventListener('keydown',async e=>{if(/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)||e.ctrlKey||e.metaKey)return;const r=item();if(e.shiftKey&&r?.corners&&['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();const q=r.corners.map(p=>[...p]),p=q[cornerIndex];if(e.key==='ArrowLeft')p[0]--;if(e.key==='ArrowRight')p[0]++;if(e.key==='ArrowUp')p[1]--;if(e.key==='ArrowDown')p[1]++;await saveEdit({corners:q});return;}if(e.key==='ArrowLeft'){e.preventDefault();navigate(-1);}if(e.key==='ArrowRight'){e.preventDefault();navigate(1);}if(e.key==='Enter'){e.preventDefault();$('accept').click();}if(e.key.toLowerCase()==='r')$('reset').click();if(e.key.toLowerCase()==='c')setCompare();if(['1','2','3','4'].includes(e.key)){cornerIndex=Number(e.key)-1;draw();}});
refresh(true);setInterval(()=>refresh(false),1800);
