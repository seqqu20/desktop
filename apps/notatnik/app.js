const $ = s => document.querySelector(s);
const LEGACY_KEY = 'pulpit-notatnik-v1';
const els = { list: $('#notesList'), search: $('#search'), title: $('#noteTitle'), body: $('#noteBody'), date: $('#noteDate'), save: $('#saveState'), count: $('#noteCount'), words: $('#wordCount'), chars: $('#charCount'), font: $('#fontSize'), fontValue: $('#fontSizeValue'), toast: $('#toast'), login: $('#loginScreen'), loginForm: $('#loginForm'), loginError: $('#loginError') };
let notes = [], activeId = null, saveTimer, saving = false;

async function api(path, options = {}) {
  const response = await fetch(path, { credentials:'same-origin', headers:{ 'Content-Type':'application/json', ...(options.headers || {}) }, ...options });
  let data = {}; try { data = await response.json(); } catch {}
  if (!response.ok) { const error = new Error(data.error || 'Błąd serwera'); error.status = response.status; throw error; }
  return data;
}
function escapeHtml(text) { const div = document.createElement('div'); div.textContent = text; return div.innerHTML; }
function formatDate(iso, long = false) { return new Date(iso).toLocaleString('pl-PL', long ? { day:'numeric', month:'long', year:'numeric', hour:'2-digit', minute:'2-digit' } : { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' }); }
function plural(count) { if (count === 1) return '1 notatka'; if (count > 1 && count < 5) return `${count} notatki`; return `${count} notatek`; }
function activeNote() { return notes.find(n => n.id === activeId); }
function toast(message) { els.toast.textContent = message; els.toast.classList.add('show'); clearTimeout(toast.timer); toast.timer = setTimeout(() => els.toast.classList.remove('show'), 1800); }

function renderList() {
  const query = els.search.value.trim().toLowerCase();
  const visible = [...notes].sort((a,b) => b.updated_at.localeCompare(a.updated_at)).filter(n => `${n.title} ${n.body}`.toLowerCase().includes(query));
  els.list.innerHTML = visible.length ? visible.map(n => `<button class="note-card${n.id === activeId ? ' active' : ''}" data-id="${n.id}"><strong>${escapeHtml(n.title || 'Bez tytułu')}</strong><p>${escapeHtml((n.body || 'Pusta notatka').replace(/\s+/g,' '))}</p><time>${formatDate(n.updated_at)}</time></button>`).join('') : '<div class="empty-list">Brak pasujących notatek.</div>';
  els.count.textContent = plural(notes.length);
  els.list.querySelectorAll('[data-id]').forEach(btn => btn.addEventListener('click', () => selectNote(btn.dataset.id)));
}
function renderEditor() {
  const note = activeNote(), disabled = !note;
  els.title.disabled = disabled; els.body.disabled = disabled; $('#deleteNote').disabled = disabled; $('#exportNote').disabled = disabled;
  els.title.value = note?.title || ''; els.body.value = note?.body || ''; els.date.textContent = note ? `Utworzono ${formatDate(note.created_at, true)}` : '—';
  els.save.textContent = 'Zapisano'; els.save.classList.remove('saving'); updateStats(); renderList();
}
async function loadNotes() {
  const data = await api('/api/notes'); notes = data.notes || [];
  await migrateLegacyNotes();
  if (!notes.length) {
    const created = await api('/api/notes', { method:'POST', body:JSON.stringify({ title:'Pierwsza notatka', body:'Witaj w Notatniku!\n\nOd teraz Twoje notatki są zapisywane w prywatnej bazie SQLite na VPS.' }) });
    notes = [created.note];
  }
  if (!notes.some(n => n.id === activeId)) activeId = notes[0].id;
  renderEditor();
}
async function migrateLegacyNotes() {
  if (localStorage.getItem(`${LEGACY_KEY}-migrated`)) return;
  try {
    const legacy = JSON.parse(localStorage.getItem(LEGACY_KEY) || '[]');
    if (Array.isArray(legacy) && legacy.length) {
      for (const note of legacy) {
        const created = await api('/api/notes', { method:'POST', body:JSON.stringify({ title:note.title || 'Bez tytułu', body:note.body || '' }) });
        notes.push(created.note);
      }
      toast(`Przeniesiono ${legacy.length} notatek do bazy`);
    }
    localStorage.setItem(`${LEGACY_KEY}-migrated`, '1');
  } catch (error) { console.warn('Migracja lokalnych notatek nie powiodła się', error); }
}
async function selectNote(id) { await flushSave(); activeId = id; renderEditor(); els.title.focus(); }
function scheduleSave() {
  const note = activeNote(); if (!note) return;
  note.title = els.title.value; note.body = els.body.value; note.updated_at = new Date().toISOString();
  els.save.textContent = 'Zapisywanie…'; els.save.classList.add('saving'); updateStats(); renderList();
  clearTimeout(saveTimer); saveTimer = setTimeout(() => flushSave(), 550);
}
async function flushSave() {
  clearTimeout(saveTimer); saveTimer = null;
  const note = activeNote(); if (!note || saving || els.save.textContent === 'Zapisano') return;
  saving = true;
  try {
    const data = await api(`/api/notes/${note.id}`, { method:'PUT', body:JSON.stringify({ title:note.title, body:note.body }) });
    note.updated_at = data.note.updated_at; els.save.textContent = 'Zapisano'; els.save.classList.remove('saving');
  } catch (error) { els.save.textContent = 'Błąd zapisu'; toast(error.message); }
  finally { saving = false; }
}
function updateStats() { const text = els.body.value; const words = text.trim() ? text.trim().split(/\s+/).length : 0; els.words.textContent = `${words} ${words === 1 ? 'słowo' : 'słów'}`; els.chars.textContent = `${text.length} znaków`; }

els.loginForm.addEventListener('submit', async event => {
  event.preventDefault(); els.loginError.textContent = '';
  try { await api('/api/login', { method:'POST', body:JSON.stringify({ password:$('#password').value }) }); els.login.hidden = true; $('#password').value = ''; await loadNotes(); }
  catch (error) { els.loginError.textContent = error.message; }
});
$('#newNote').addEventListener('click', async () => { await flushSave(); try { const data = await api('/api/notes', { method:'POST', body:JSON.stringify({ title:'Nowa notatka', body:'' }) }); notes.unshift(data.note); activeId = data.note.id; renderEditor(); els.title.select(); toast('Utworzono nową notatkę'); } catch (error) { toast(error.message); } });
$('#deleteNote').addEventListener('click', async () => { const note = activeNote(); if (!note || !confirm(`Usunąć notatkę „${note.title || 'Bez tytułu'}”?`)) return; try { await api(`/api/notes/${note.id}`, { method:'DELETE' }); notes = notes.filter(n => n.id !== note.id); activeId = notes[0]?.id || null; if (!notes.length) { const data = await api('/api/notes', { method:'POST', body:JSON.stringify({ title:'Nowa notatka', body:'' }) }); notes = [data.note]; activeId = data.note.id; } renderEditor(); toast('Notatka usunięta'); } catch (error) { toast(error.message); } });
$('#exportNote').addEventListener('click', () => { const note = activeNote(); if (!note) return; const blob = new Blob([`${note.title}\n\n${note.body}`], { type:'text/plain;charset=utf-8' }); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = `${(note.title || 'notatka').replace(/[\\/:*?"<>|]/g,'-')}.txt`; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 2000); });
$('#logoutNote').addEventListener('click', async () => { await flushSave(); try { await api('/api/logout', { method:'POST', body:'{}' }); } catch {} notes = []; activeId = null; renderEditor(); els.login.hidden = false; $('#password').focus(); });
els.title.addEventListener('input', scheduleSave); els.body.addEventListener('input', scheduleSave); els.search.addEventListener('input', renderList);
els.font.addEventListener('input', () => { els.fontValue.value = els.font.value; els.body.style.fontSize = `${els.font.value}px`; localStorage.setItem(`${LEGACY_KEY}-font`, els.font.value); });
document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); flushSave(); toast('Notatka zapisana'); } });
window.addEventListener('beforeunload', () => { if (saveTimer) flushSave(); });

const savedFont = localStorage.getItem(`${LEGACY_KEY}-font`) || '18'; els.font.value = savedFont; els.fontValue.value = savedFont; els.body.style.fontSize = `${savedFont}px`;
(async function init() { try { const session = await api('/api/session'); if (session.authenticated) { els.login.hidden = true; await loadNotes(); } } catch { els.loginError.textContent = 'Brak połączenia z bazą danych.'; } })();
