/* global pdfjsLib, fabric, PDFLib */
pdfjsLib.GlobalWorkerOptions.workerSrc = '../../assets/vendor/pdf.worker.min.js';

const $ = (s) => document.querySelector(s);
const els = {
  workspace: $('#workspace'), pdfInput: $('#pdfInput'), imageInput: $('#imageInput'), uploadBtn: $('#uploadBtn'), newFileBtn: $('#newFileBtn'),
  exportBtn: $('#exportBtn'), emptyState: $('#emptyState'), canvasZone: $('#canvasZone'), pdfCanvas: $('#pdfCanvas'), pageStage: $('#pageStage'),
  thumbs: $('#thumbs'), pageCount: $('#pageCount'), fileName: $('#fileName'), toolbar: $('#toolbar'), selectionBar: $('#selectionBar'),
  color: $('#colorPicker'), swatch: $('#colorSwatch'), brush: $('#brushSize'), brushOutput: $('#brushOutput'), deleteBtn: $('#deleteBtn'),
  undo: $('#undoBtn'), redo: $('#redoBtn'), toast: $('#toast'), busy: $('#busy'), busyText: $('#busyText'), zoomValue: $('#zoomValue')
};

let pdfDoc = null, pdfBytes = null, fabricCanvas = null, pageNum = 1, pageStates = {}, zoom = 1;
let currentTool = 'select', history = [], historyIndex = -1, restoring = false, renderToken = 0;
const RENDER_SCALE = 1.35;

function toast(message) { els.toast.textContent = message; els.toast.classList.add('show'); clearTimeout(toast.t); toast.t = setTimeout(() => els.toast.classList.remove('show'), 2200); }
function busy(on, text = 'Przetwarzam…') { els.busy.hidden = !on; els.busyText.textContent = text; }
function setTool(tool) {
  currentTool = tool;
  document.querySelectorAll('.tool').forEach(b => b.classList.toggle('active', b.dataset.tool === tool));
  if (!fabricCanvas) return;
  fabricCanvas.isDrawingMode = tool === 'draw';
  fabricCanvas.selection = tool === 'select';
  fabricCanvas.forEachObject(o => { o.selectable = tool === 'select'; o.evented = tool === 'select'; });
  fabricCanvas.defaultCursor = tool === 'text' ? 'text' : tool === 'draw' ? 'crosshair' : 'default';
  if (tool !== 'select') fabricCanvas.discardActiveObject().requestRenderAll();
}

function snapshot() {
  if (!fabricCanvas || restoring) return;
  const json = JSON.stringify(fabricCanvas.toJSON(['name']));
  if (history[historyIndex] === json) return;
  history = history.slice(0, historyIndex + 1); history.push(json); historyIndex = history.length - 1;
  if (history.length > 60) { history.shift(); historyIndex--; }
  updateHistoryButtons();
}
function updateHistoryButtons() { els.undo.disabled = historyIndex <= 0; els.redo.disabled = historyIndex >= history.length - 1; }
function restoreHistory(index) {
  if (!fabricCanvas || !history[index]) return;
  restoring = true; historyIndex = index;
  fabricCanvas.loadFromJSON(history[index], () => { restoring = false; setTool(currentTool); fabricCanvas.requestRenderAll(); updateHistoryButtons(); });
}

async function loadPdf(file) {
  if (!file || file.type !== 'application/pdf') return toast('Wybierz plik PDF.');
  if (file.size > 100 * 1024 * 1024) return toast('Plik jest większy niż 100 MB.');
  busy(true, 'Otwieram dokument…');
  try {
    pdfBytes = new Uint8Array(await file.arrayBuffer());
    pdfDoc = await pdfjsLib.getDocument({ data: pdfBytes.slice() }).promise;
    pageNum = 1; pageStates = {}; history = []; historyIndex = -1;
    els.fileName.textContent = file.name; els.pageCount.textContent = pdfDoc.numPages; els.exportBtn.disabled = false;
    els.emptyState.hidden = true; els.canvasZone.hidden = false; els.workspace.classList.remove('empty');
    await renderPage(1); renderThumbnails(); toast(`Otwarto: ${file.name}`);
  } catch (err) { console.error(err); toast('Nie udało się otworzyć tego PDF-a.'); }
  finally { busy(false); }
}

async function renderPage(number) {
  const token = ++renderToken;
  saveCurrentPage();
  pageNum = number;
  document.querySelectorAll('.thumb-item').forEach((x, i) => x.classList.toggle('active', i + 1 === number));
  const page = await pdfDoc.getPage(number); const viewport = page.getViewport({ scale: RENDER_SCALE });
  els.pdfCanvas.width = viewport.width; els.pdfCanvas.height = viewport.height;
  els.pageStage.style.width = `${viewport.width}px`; els.pageStage.style.height = `${viewport.height}px`;
  await page.render({ canvasContext: els.pdfCanvas.getContext('2d'), viewport }).promise;
  if (token !== renderToken) return;
  if (fabricCanvas) fabricCanvas.dispose();
  fabricCanvas = new fabric.Canvas('editCanvas', { width: viewport.width, height: viewport.height, preserveObjectStacking: true, selectionColor: 'rgba(239,79,63,.08)', selectionBorderColor: '#ef4f3f' });
  fabric.Object.prototype.set({ transparentCorners: false, cornerColor: '#ef4f3f', cornerStrokeColor: '#fff', borderColor: '#ef4f3f', cornerSize: 10, padding: 2 });
  ['object:added','object:modified','object:removed'].forEach(evt => fabricCanvas.on(evt, snapshot));
  fabricCanvas.on('selection:created', updateSelection); fabricCanvas.on('selection:updated', updateSelection); fabricCanvas.on('selection:cleared', updateSelection);
  fabricCanvas.on('mouse:down', (opt) => {
    if (currentTool === 'text' && !opt.target) addText(opt.pointer.x, opt.pointer.y);
  });
  if (pageStates[number]) {
    restoring = true;
    await new Promise(resolve => fabricCanvas.loadFromJSON(pageStates[number], () => { restoring = false; resolve(); }));
  }
  history = [JSON.stringify(fabricCanvas.toJSON(['name']))]; historyIndex = 0; updateHistoryButtons(); setTool(currentTool); updateSelection(); applyZoom();
}

function saveCurrentPage() { if (fabricCanvas && pageNum) pageStates[pageNum] = fabricCanvas.toJSON(['name']); }

async function renderThumbnails() {
  els.thumbs.innerHTML = '';
  for (let n = 1; n <= pdfDoc.numPages; n++) {
    const wrap = document.createElement('button'); wrap.className = `thumb-item${n === pageNum ? ' active' : ''}`; wrap.type = 'button';
    wrap.innerHTML = `<span class="thumb-number">${n}</span>`; const canvas = document.createElement('canvas'); wrap.appendChild(canvas); els.thumbs.appendChild(wrap);
    wrap.addEventListener('click', () => renderPage(n));
    const page = await pdfDoc.getPage(n), vp = page.getViewport({ scale: .24 }); canvas.width = vp.width; canvas.height = vp.height;
    await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp }).promise;
  }
}

function addText(x = 80, y = 80) {
  const text = new fabric.IText('Wpisz tekst', { left: x, top: y, fill: els.color.value, fontFamily: 'Arial', fontSize: 26, name: 'text' });
  fabricCanvas.add(text).setActiveObject(text); setTool('select'); text.enterEditing(); text.selectAll(); fabricCanvas.requestRenderAll();
}
function addImage(file) {
  if (!file) return;
  const reader = new FileReader(); reader.onload = () => fabric.Image.fromURL(reader.result, img => {
    const maxW = fabricCanvas.width * .55, maxH = fabricCanvas.height * .55, scale = Math.min(maxW / img.width, maxH / img.height, 1);
    img.set({ left: fabricCanvas.width / 2, top: fabricCanvas.height / 2, originX: 'center', originY: 'center', name: 'image' }).scale(scale);
    fabricCanvas.add(img).setActiveObject(img).requestRenderAll(); setTool('select'); toast('Zdjęcie dodane — przeciągnij rogi, aby zmienić rozmiar.');
  }, { crossOrigin: 'anonymous' }); reader.readAsDataURL(file);
}
function updateSelection() {
  if (!fabricCanvas) return;
  const active = fabricCanvas.getActiveObject(), has = !!active;
  els.selectionBar.classList.toggle('visible', has); els.deleteBtn.disabled = !has;
}
function selected() { return fabricCanvas && fabricCanvas.getActiveObject(); }
function fitSelected(mode) {
  const o = selected(); if (!o) return; const pad = 34;
  if (mode === 'width') o.scaleToWidth(fabricCanvas.width - pad * 2); else {
    const s = Math.min((fabricCanvas.width - pad * 2) / o.width, (fabricCanvas.height - pad * 2) / o.height); o.scale(s);
  }
  o.set({ left: fabricCanvas.width / 2, top: fabricCanvas.height / 2, originX: 'center', originY: 'center' }).setCoords(); fabricCanvas.requestRenderAll(); snapshot();
}
function deleteSelected() { const o = selected(); if (!o) return; if (o.type === 'activeSelection') o.forEachObject(x => fabricCanvas.remove(x)); else fabricCanvas.remove(o); fabricCanvas.discardActiveObject().requestRenderAll(); updateSelection(); }
function duplicateSelected() { const o = selected(); if (!o) return; o.clone(clone => { clone.set({ left: o.left + 18, top: o.top + 18 }); fabricCanvas.add(clone).setActiveObject(clone).requestRenderAll(); }); }

async function exportPdf() {
  if (!pdfDoc) return; busy(true, 'Składam gotowy PDF…');
  try {
    saveCurrentPage(); const out = await PDFLib.PDFDocument.load(pdfBytes.slice()); const pages = out.getPages();
    for (let n = 1; n <= pages.length; n++) {
      const state = pageStates[n]; if (!state || !(state.objects || []).length) continue;
      const p = await pdfDoc.getPage(n), vp = p.getViewport({ scale: RENDER_SCALE });
      const tempEl = document.createElement('canvas'); tempEl.width = vp.width; tempEl.height = vp.height;
      const temp = new fabric.StaticCanvas(tempEl, { width: vp.width, height: vp.height });
      await new Promise(resolve => temp.loadFromJSON(state, () => { temp.renderAll(); resolve(); }));
      const data = temp.toDataURL({ format: 'png', multiplier: 2 }); const png = await out.embedPng(data); const { width, height } = pages[n - 1].getSize();
      pages[n - 1].drawImage(png, { x: 0, y: 0, width, height }); temp.dispose();
    }
    const bytes = await out.save(); const blob = new Blob([bytes], { type: 'application/pdf' }); const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = `${els.fileName.textContent.replace(/\.pdf$/i, '')}-edytowany.pdf`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000); toast('Gotowe — PDF został pobrany.');
  } catch (e) { console.error(e); toast('Nie udało się wyeksportować PDF-a.'); } finally { busy(false); }
}

function applyZoom() { els.pageStage.style.transform = `scale(${zoom})`; els.zoomValue.textContent = `${Math.round(zoom * 100)}%`; els.canvasZone.style.paddingBottom = `${38 + Math.max(0, (zoom - 1) * els.pageStage.offsetHeight)}px`; }

els.uploadBtn.addEventListener('click', () => els.pdfInput.click()); els.newFileBtn.addEventListener('click', () => els.pdfInput.click());
els.pdfInput.addEventListener('change', e => loadPdf(e.target.files[0])); els.imageInput.addEventListener('change', e => addImage(e.target.files[0]));
els.toolbar.addEventListener('click', e => { const b = e.target.closest('[data-tool]'); if (!b || !pdfDoc) return; if (b.dataset.tool === 'image') els.imageInput.click(); else { setTool(b.dataset.tool); if (b.dataset.tool === 'text') toast('Kliknij na stronie, aby dodać tekst.'); } });
els.color.addEventListener('input', () => { els.swatch.style.background = els.color.value; if (fabricCanvas) { fabricCanvas.freeDrawingBrush.color = els.color.value; const o = selected(); if (o && o.name === 'text') { o.set('fill', els.color.value); fabricCanvas.requestRenderAll(); snapshot(); } } });
els.brush.addEventListener('input', () => { els.brushOutput.value = els.brush.value; if (fabricCanvas) fabricCanvas.freeDrawingBrush.width = Number(els.brush.value); });
els.deleteBtn.addEventListener('click', deleteSelected); els.undo.addEventListener('click', () => restoreHistory(historyIndex - 1)); els.redo.addEventListener('click', () => restoreHistory(historyIndex + 1));
$('#fitWidthBtn').addEventListener('click', () => fitSelected('width')); $('#fitPageBtn').addEventListener('click', () => fitSelected('page'));
$('#rotateBtn').addEventListener('click', () => { const o = selected(); if (o) { o.rotate((o.angle + 90) % 360).setCoords(); fabricCanvas.requestRenderAll(); snapshot(); } });
$('#duplicateBtn').addEventListener('click', duplicateSelected); $('#backBtn').addEventListener('click', () => { const o = selected(); if (o) { fabricCanvas.sendBackwards(o); snapshot(); } });
$('#frontBtn').addEventListener('click', () => { const o = selected(); if (o) { fabricCanvas.bringToFront(o); snapshot(); } }); els.exportBtn.addEventListener('click', exportPdf);
$('#zoomOut').addEventListener('click', () => { zoom = Math.max(.5, zoom - .1); applyZoom(); }); $('#zoomIn').addEventListener('click', () => { zoom = Math.min(1.8, zoom + .1); applyZoom(); });
document.addEventListener('keydown', e => {
  if (!pdfDoc || (document.activeElement && document.activeElement.isContentEditable)) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? restoreHistory(historyIndex + 1) : restoreHistory(historyIndex - 1); }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') { e.preventDefault(); restoreHistory(historyIndex + 1); }
  else if ((e.key === 'Delete' || e.key === 'Backspace') && selected() && !selected().isEditing) { e.preventDefault(); deleteSelected(); }
  else if (e.key.toLowerCase() === 'v') setTool('select'); else if (e.key.toLowerCase() === 't') { setTool('text'); toast('Kliknij na stronie, aby dodać tekst.'); } else if (e.key.toLowerCase() === 'd') setTool('draw');
});
window.addEventListener('beforeunload', e => { if (pdfDoc) { e.preventDefault(); e.returnValue = ''; } });
