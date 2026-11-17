const $ = s => document.querySelector(s);
const STORAGE_KEY = 'pulpit-notatnik-v1';
const els = { list: $('#notesList'), search: $('#search'), title: $('#noteTitle'), body: $('#noteBody'), date: $('#noteDate'), save: $('#saveState'), count: $('#noteCount'), words: $('#wordCount'), chars: $('#charCount'), font: $('#fontSize'), fontValue: $('#fontSizeValue'), toast: $('#toast') };
let notes = loadNotes();
let activeId = localStorage.getItem(`${STORAGE_KEY}-active`) || notes[0]?.id;
let saveTimer;

if (!notes.length) {
  const first = createNote('Pierwsza notatka', 'Witaj w Notatniku!\n\nTwoje notatki zapisują się automatycznie w tej przeglądarce. Możesz tworzyć kolejne, wyszukiwać je i pobierać jako pliki tekstowe.');
  notes = [first]; activeId = first.id; persist();
}
if (!notes.some(n => n.id === activeId)) activeId = notes[0].id;

function createNote(title = 'Nowa notatka', body = '') {
  const now = new Date().toISOString();
  return { id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`, title, body, createdAt: now, updatedAt: now };
}
function loadNotes() {
  try { const value = JSON.parse(localStorage.getItem(STORAGE_KEY)); return Array.isArray(value) ? value : []; }
  catch { return []; }
}
function persist() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(notes));
  localStorage.setItem(`${STORAGE_KEY}-active`, activeId || '');
}
function activeNote() { return notes.find(n => n.id === activeId); }
function escapeHtml(text) { const div = document.createElement('div'); div.textContent = text; return div.innerHTML; }
function formatDate(iso, long = false) {
  return new Date(iso).toLocaleString('pl-PL', long ? { day:'numeric', month:'long', year:'numeric', hour:'2-digit', minute:'2-digit' } : { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' });
}
function plural(count) { if (count === 1) return '1 notatka'; if (count > 1 && count < 5) return `${count} notatki`; return `${count} notatek`; }
function renderList() {
  const query = els.search.value.trim().toLowerCase();
  const visible = [...notes].sort((a,b) => b.updatedAt.localeCompare(a.updatedAt)).filter(n => `${n.title} ${n.body}`.toLowerCase().includes(query));
  els.list.innerHTML = visible.length ? visible.map(n => `<button class="note-card${n.id === activeId ? ' active' : ''}" data-id="${n.id}"><strong>${escapeHtml(n.title || 'Bez tytułu')}</strong><p>${escapeHtml((n.body || 'Pusta notatka').replace(/\s+/g,' '))}</p><time>${formatDate(n.updatedAt)}</time></button>`).join('') : '<div class="empty-list">Brak pasujących notatek.</div>';
  els.count.textContent = plural(notes.length);
  els.list.querySelectorAll('[data-id]').forEach(btn => btn.addEventListener('click', () => selectNote(btn.dataset.id)));
}
function renderEditor() {
  const note = activeNote();
  const disabled = !note;
  els.title.disabled = disabled; els.body.disabled = disabled; $('#deleteNote').disabled = disabled; $('#exportNote').disabled = disabled;
  els.title.value = note?.title || ''; els.body.value = note?.body || ''; els.date.textContent = note ? `Utworzono ${formatDate(note.createdAt, true)}` : '—';
  updateStats(); renderList();
}
function selectNote(id) { flushSave(); activeId = id; persist(); renderEditor(); els.title.focus(); }
function scheduleSave() {
  const note = activeNote(); if (!note) return;
  note.title = els.title.value; note.body = els.body.value; note.updatedAt = new Date().toISOString();
  els.save.textContent = 'Zapisywanie…'; els.save.classList.add('saving'); updateStats(); renderList();
  clearTimeout(saveTimer); saveTimer = setTimeout(flushSave, 450);
}
function flushSave() {
  clearTimeout(saveTimer); saveTimer = null; persist(); els.save.textContent = 'Zapisano'; els.save.classList.remove('saving');
}
function updateStats() {
  const text = els.body.value; const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  els.words.textContent = `${words} ${words === 1 ? 'słowo' : 'słów'}`; els.chars.textContent = `${text.length} znaków`;
}
function toast(message) { els.toast.textContent = message; els.toast.classList.add('show'); clearTimeout(toast.timer); toast.timer = setTimeout(() => els.toast.classList.remove('show'), 1800); }

$('#newNote').addEventListener('click', () => {
  flushSave(); const note = createNote(); notes.unshift(note); activeId = note.id; persist(); renderEditor(); els.title.select(); toast('Utworzono nową notatkę');
});
$('#deleteNote').addEventListener('click', () => {
  const note = activeNote(); if (!note || !confirm(`Usunąć notatkę „${note.title || 'Bez tytułu'}”?`)) return;
  notes = notes.filter(n => n.id !== note.id); activeId = notes[0]?.id || null;
  if (!notes.length) { const fresh = createNote(); notes = [fresh]; activeId = fresh.id; }
  persist(); renderEditor(); toast('Notatka usunięta');
});
$('#exportNote').addEventListener('click', () => {
  const note = activeNote(); if (!note) return;
  const blob = new Blob([`${note.title}\n\n${note.body}`], { type:'text/plain;charset=utf-8' });
  const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = `${(note.title || 'notatka').replace(/[\\/:*?"<>|]/g,'-')}.txt`; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 2000);
});
els.title.addEventListener('input', scheduleSave); els.body.addEventListener('input', scheduleSave); els.search.addEventListener('input', renderList);
els.font.addEventListener('input', () => { els.fontValue.value = els.font.value; els.body.style.fontSize = `${els.font.value}px`; localStorage.setItem(`${STORAGE_KEY}-font`, els.font.value); });
document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); flushSave(); toast('Notatka zapisana'); } });
window.addEventListener('beforeunload', flushSave);

const savedFont = localStorage.getItem(`${STORAGE_KEY}-font`) || '18'; els.font.value = savedFont; els.fontValue.value = savedFont; els.body.style.fontSize = `${savedFont}px`;
renderEditor();
