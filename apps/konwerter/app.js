/* global PDFLib */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const state = { images: [], imageResults: [], pdfs: [] };
const els = { imageInput:$('#imageInput'), imageList:$('#imageList'), format:$('#imageFormat'), maxWidth:$('#maxWidth'), quality:$('#quality'), qualityValue:$('#qualityValue'), convertImages:$('#convertImages'), downloadAll:$('#downloadAll'), pdfInput:$('#pdfInput'), pdfList:$('#pdfList'), pdfCount:$('#pdfCount'), merge:$('#mergePdfs'), toast:$('#toast'), busy:$('#busy'), busyText:$('#busyText') };

function toast(message){ els.toast.textContent=message; els.toast.classList.add('show'); clearTimeout(toast.t); toast.t=setTimeout(()=>els.toast.classList.remove('show'),1900); }
function busy(on,text='Przetwarzam…'){ els.busy.hidden=!on; els.busyText.textContent=text; }
function size(bytes){ if(bytes<1024)return `${bytes} B`; if(bytes<1048576)return `${(bytes/1024).toFixed(1)} KB`; return `${(bytes/1048576).toFixed(1)} MB`; }
function safeName(name){ return name.replace(/[\\/:*?"<>|]/g,'-'); }
function download(blob,name){ const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=safeName(name); a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),3000); }

$$('.tabs button').forEach(button=>button.addEventListener('click',()=>{ $$('.tabs button').forEach(x=>x.classList.toggle('active',x===button)); $$('.panel').forEach(x=>x.classList.toggle('active',x.id===`panel-${button.dataset.tab}`)); }));

function setupDrop(zone,input,handler){ zone.addEventListener('dragover',e=>{e.preventDefault();zone.classList.add('drag')}); zone.addEventListener('dragleave',()=>zone.classList.remove('drag')); zone.addEventListener('drop',e=>{e.preventDefault();zone.classList.remove('drag');handler([...e.dataTransfer.files])}); input.addEventListener('change',()=>handler([...input.files])); }
setupDrop($('#imageDrop'),els.imageInput,addImages); setupDrop($('#pdfDrop'),els.pdfInput,addPdfs);

function addImages(files){ const valid=files.filter(f=>/^image\/(jpeg|png|webp)$/.test(f.type)); state.images.push(...valid.map(file=>({id:crypto.randomUUID(),file,url:URL.createObjectURL(file)}))); renderImages(); if(valid.length!==files.length)toast('Pominięto nieobsługiwane pliki'); }
function renderImages(){ els.convertImages.disabled=!state.images.length; if(!state.images.length){els.imageList.innerHTML='<div class="empty">Nie wybrano jeszcze obrazów.</div>';return} els.imageList.innerHTML=state.images.map(item=>`<div class="file-row"><img class="file-thumb" src="${item.url}" alt=""><div class="file-info"><strong>${item.file.name}</strong><span>${size(item.file.size)} · ${item.file.type.replace('image/','').toUpperCase()}</span></div><div class="file-actions"><button class="remove" data-remove-image="${item.id}">×</button></div></div>`).join(''); $$('[data-remove-image]').forEach(b=>b.addEventListener('click',()=>{const i=state.images.findIndex(x=>x.id===b.dataset.removeImage);if(i>-1){URL.revokeObjectURL(state.images[i].url);state.images.splice(i,1);renderImages()}})); }
els.quality.addEventListener('input',()=>els.qualityValue.textContent=`${els.quality.value}%`);
els.format.addEventListener('change',()=>{ const png=els.format.value==='image/png'; els.quality.disabled=png; });

async function imageBitmap(file){ if('createImageBitmap' in window)return createImageBitmap(file,{imageOrientation:'from-image'}); return new Promise((resolve,reject)=>{const img=new Image();const url=URL.createObjectURL(file);img.onload=()=>{URL.revokeObjectURL(url);resolve(img)};img.onerror=reject;img.src=url}); }
els.convertImages.addEventListener('click',async()=>{ busy(true,'Konwertuję obrazy…'); state.imageResults.forEach(r=>URL.revokeObjectURL(r.url)); state.imageResults=[]; try{ for(const item of state.images){ const bitmap=await imageBitmap(item.file); const limit=Number(els.maxWidth.value); const width=limit?Math.min(bitmap.width,limit):bitmap.width; const height=Math.round(bitmap.height*(width/bitmap.width)); const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;const ctx=canvas.getContext('2d');if(els.format.value==='image/jpeg'){ctx.fillStyle='#fff';ctx.fillRect(0,0,width,height)}ctx.drawImage(bitmap,0,0,width,height);bitmap.close?.();const blob=await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('Nie udało się utworzyć obrazu')),els.format.value,Number(els.quality.value)/100));const ext={'image/jpeg':'jpg','image/png':'png','image/webp':'webp'}[els.format.value];const name=`${item.file.name.replace(/\.[^.]+$/,'')}.${ext}`;state.imageResults.push({blob,name,url:URL.createObjectURL(blob),width,height}); } renderImageResults();toast('Obrazy są gotowe'); }catch(e){console.error(e);toast('Nie udało się przekonwertować obrazu')}finally{busy(false)} });
function renderImageResults(){ els.imageList.innerHTML=state.imageResults.map((result,i)=>`<div class="file-row done"><img class="file-thumb" src="${result.url}" alt=""><div class="file-info"><strong>${result.name}</strong><span class="result-tag">${result.width} × ${result.height} · ${size(result.blob.size)}</span></div><button class="download-one" data-download-image="${i}">Pobierz</button></div>`).join(''); $$('[data-download-image]').forEach(b=>b.addEventListener('click',()=>{const r=state.imageResults[Number(b.dataset.downloadImage)];download(r.blob,r.name)})); els.downloadAll.hidden=state.imageResults.length<2; }
els.downloadAll.addEventListener('click',async()=>{for(const r of state.imageResults){download(r.blob,r.name);await new Promise(resolve=>setTimeout(resolve,180))}});

function addPdfs(files){ const valid=files.filter(f=>f.type==='application/pdf'||f.name.toLowerCase().endsWith('.pdf'));state.pdfs.push(...valid.map(file=>({id:crypto.randomUUID(),file})));renderPdfs();if(valid.length!==files.length)toast('Pominięto pliki inne niż PDF'); }
function renderPdfs(){ els.pdfCount.textContent=state.pdfs.length;els.merge.disabled=state.pdfs.length<2;if(!state.pdfs.length){els.pdfList.innerHTML='<div class="empty">Dodaj co najmniej dwa pliki PDF.</div>';return}els.pdfList.innerHTML=state.pdfs.map((item,i)=>`<div class="file-row"><div class="file-type">PDF</div><div class="file-info"><strong>${i+1}. ${item.file.name}</strong><span>${size(item.file.size)}</span></div><div class="file-actions"><button data-up="${item.id}" ${i===0?'disabled':''}>↑</button><button data-down="${item.id}" ${i===state.pdfs.length-1?'disabled':''}>↓</button><button class="remove" data-remove-pdf="${item.id}">×</button></div></div>`).join('');$$('[data-up]').forEach(b=>b.addEventListener('click',()=>movePdf(b.dataset.up,-1)));$$('[data-down]').forEach(b=>b.addEventListener('click',()=>movePdf(b.dataset.down,1)));$$('[data-remove-pdf]').forEach(b=>b.addEventListener('click',()=>{state.pdfs=state.pdfs.filter(x=>x.id!==b.dataset.removePdf);renderPdfs()})); }
function movePdf(id,direction){const i=state.pdfs.findIndex(x=>x.id===id),j=i+direction;if(i<0||j<0||j>=state.pdfs.length)return;[state.pdfs[i],state.pdfs[j]]=[state.pdfs[j],state.pdfs[i]];renderPdfs();}
els.merge.addEventListener('click',async()=>{busy(true,'Łączę dokumenty PDF…');try{const output=await PDFLib.PDFDocument.create();for(const item of state.pdfs){const source=await PDFLib.PDFDocument.load(await item.file.arrayBuffer());const pages=await output.copyPages(source,source.getPageIndices());pages.forEach(p=>output.addPage(p))}const bytes=await output.save();download(new Blob([bytes],{type:'application/pdf'}),'polaczone-dokumenty.pdf');toast('Połączony PDF jest gotowy')}catch(e){console.error(e);toast('Nie udało się połączyć PDF-ów')}finally{busy(false)}});

$('#textInput').addEventListener('change',async e=>{const file=e.target.files[0];if(!file)return;$('#textContent').value=await file.text();$('#documentTitle').value=file.name.replace(/\.[^.]+$/,'');});
$('#textToPdf').addEventListener('click',async()=>{const text=$('#textContent').value,title=$('#documentTitle').value.trim()||'Dokument';if(!text.trim())return toast('Wpisz treść dokumentu');busy(true,'Tworzę dokument PDF…');try{const pdf=await PDFLib.PDFDocument.create();const W=1240,H=1754,M=105,fontSize=Number($('#textSize').value)*2,lineHeight=Math.round(fontSize*1.5);const canvas=document.createElement('canvas');canvas.width=W;canvas.height=H;const ctx=canvas.getContext('2d');ctx.font=`${fontSize}px Arial`;const maxWidth=W-M*2;const lines=[];for(const paragraph of text.replace(/\r/g,'').split('\n')){if(!paragraph){lines.push('');continue}let line='';for(const word of paragraph.split(/\s+/)){const test=line?`${line} ${word}`:word;if(ctx.measureText(test).width>maxWidth&&line){lines.push(line);line=word}else line=test}lines.push(line)}let index=0,pageNumber=0;while(index<lines.length||pageNumber===0){ctx.fillStyle='#fff';ctx.fillRect(0,0,W,H);ctx.fillStyle='#18201f';let y=M;if(pageNumber===0){ctx.font='bold 52px Arial';ctx.fillText(title,M,y);y+=85;ctx.strokeStyle='#d8dedc';ctx.beginPath();ctx.moveTo(M,y-28);ctx.lineTo(W-M,y-28);ctx.stroke()}ctx.font=`${fontSize}px Arial`;while(index<lines.length&&y<=H-M){ctx.fillText(lines[index],M,y);y+=lineHeight;index++}ctx.fillStyle='#8a9391';ctx.font='20px Arial';ctx.fillText(`${pageNumber+1}`,W-M-10,H-45);const png=await pdf.embedPng(canvas.toDataURL('image/png'));const page=pdf.addPage([595.28,841.89]);page.drawImage(png,{x:0,y:0,width:595.28,height:841.89});pageNumber++}const bytes=await pdf.save();download(new Blob([bytes],{type:'application/pdf'}),`${title}.pdf`);toast('Dokument PDF jest gotowy')}catch(e){console.error(e);toast('Nie udało się utworzyć PDF-a')}finally{busy(false)}});

let ocrFile=null;
setupDrop($('#ocrDrop'),$('#ocrInput'),files=>selectOcrFile(files[0]));
function selectOcrFile(file){
  if(!file)return;
  if(!(file.type==='application/pdf'||file.name.toLowerCase().endsWith('.pdf')))return toast('Wybierz plik PDF');
  if(file.size>20*1024*1024)return toast('PDF może mieć maksymalnie 20 MB');
  ocrFile=file;
  $('#ocrSelected').hidden=false;
  $('#ocrSelected').innerHTML=`<div class="file-row"><div class="file-type">PDF</div><div class="file-info"><strong>${file.name}</strong><span>${size(file.size)} · gotowy do OCR</span></div></div>`;
  $('#ocrConvert').disabled=false;
}
async function ocrApi(path,options={}){
  const response=await fetch(path,{credentials:'same-origin',...options});
  if(response.ok)return response;
  let message='Błąd konwersji';try{message=(await response.json()).error||message}catch{}
  const error=new Error(message);error.status=response.status;throw error;
}
async function ensureOcrLogin(){
  const session=await (await ocrApi('/api/session')).json();
  if(session.authenticated){$('#ocrAuth').hidden=true;return true}
  $('#ocrAuth').hidden=false;
  const password=$('#ocrPassword').value;
  if(!password){$('#ocrPassword').focus();$('#ocrAuthError').textContent='Wpisz hasło do prywatnych narzędzi.';return false}
  try{await ocrApi('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password})});$('#ocrPassword').value='';$('#ocrAuth').hidden=true;$('#ocrAuthError').textContent='';return true}catch(error){$('#ocrAuthError').textContent=error.message;return false}
}
$('#ocrPassword').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();$('#ocrConvert').click()}});
$('#ocrConvert').addEventListener('click',async()=>{
  if(!ocrFile)return toast('Najpierw wybierz PDF');
  try{
    if(!await ensureOcrLogin())return;
    busy(true,'OCR analizuje dokument… To może potrwać kilka minut.');
    const response=await ocrApi('/api/ocr/pdf-to-word',{method:'POST',headers:{'Content-Type':'application/pdf','X-Filename':encodeURIComponent(ocrFile.name)},body:ocrFile});
    const blob=await response.blob();
    download(blob,`${ocrFile.name.replace(/\.pdf$/i,'')}-OCR.docx`);
    toast('Dokument Word jest gotowy');
  }catch(error){
    console.error(error);if(error.status===401)$('#ocrAuth').hidden=false;toast(error.message);
  }finally{busy(false)}
});
