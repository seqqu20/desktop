const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const apps = {
  pdf: { name: 'PDF Studio', icon: '<span class="mini-pdf">P</span>' },
  notatnik: { name: 'Notatnik', icon: '<span class="mini-note">N</span>' }
};
let topZ = 20;
const startMenu = $('#startMenu');
const startButton = $('#startButton');
const runningApps = $('#runningApps');

function bringToFront(win) {
  topZ += 1; win.style.zIndex = topZ;
  $$('.task-app').forEach(b => b.classList.toggle('active', b.dataset.app === win.dataset.app));
}
function ensureTask(app) {
  let task = $(`.task-app[data-app="${app}"]`);
  if (task) return task;
  task = document.createElement('button'); task.className = 'task-app active'; task.dataset.app = app;
  task.innerHTML = `${apps[app].icon}<span>${apps[app].name}</span>`;
  task.addEventListener('click', () => {
    const win = $(`#window-${app}`);
    if (win.classList.contains('minimized')) { win.classList.remove('minimized'); bringToFront(win); }
    else if (Number(win.style.zIndex) === topZ && !win.hidden) minimizeWindow(win); else bringToFront(win);
  });
  runningApps.appendChild(task); return task;
}
function openApp(app) {
  const win = $(`#window-${app}`); if (!win) return;
  win.hidden = false; win.classList.remove('minimized'); ensureTask(app); bringToFront(win); closeStart();
}
function minimizeWindow(win) { win.classList.add('minimized'); $(`.task-app[data-app="${win.dataset.app}"]`)?.classList.remove('active'); }
function closeWindow(win) { win.hidden = true; win.classList.remove('minimized'); $(`.task-app[data-app="${win.dataset.app}"]`)?.remove(); }
function toggleMaximize(win) { win.classList.toggle('maximized'); bringToFront(win); }
function closeStart() { startMenu.hidden = true; startButton.classList.remove('active'); }

$$('[data-open]').forEach(el => {
  el.addEventListener(el.classList.contains('desktop-icon') ? 'dblclick' : 'click', () => openApp(el.dataset.open));
  if (el.classList.contains('desktop-icon')) el.addEventListener('click', () => el.focus());
});
startButton.addEventListener('click', e => { e.stopPropagation(); startMenu.hidden = !startMenu.hidden; startButton.classList.toggle('active', !startMenu.hidden); if (!startMenu.hidden) $('#appSearch').focus(); });
document.addEventListener('pointerdown', e => { if (!startMenu.contains(e.target) && !startButton.contains(e.target)) closeStart(); });
$('#showDesktop').addEventListener('click', () => $$('.window:not([hidden])').forEach(minimizeWindow));
$('#appSearch').addEventListener('input', e => { const q = e.target.value.toLowerCase(); $$('.start-app').forEach(x => x.hidden = !x.textContent.toLowerCase().includes(q)); });

$$('.window').forEach(win => {
  win.addEventListener('pointerdown', () => bringToFront(win));
  $('.window-controls', win).addEventListener('click', e => {
    const action = e.target.dataset.action; if (!action) return;
    if (action === 'close') closeWindow(win); if (action === 'minimize') minimizeWindow(win); if (action === 'maximize') toggleMaximize(win);
  });
  const bar = $('.window-titlebar', win); let drag = null;
  bar.addEventListener('dblclick', e => { if (!e.target.closest('.window-controls')) toggleMaximize(win); });
  bar.addEventListener('pointerdown', e => {
    if (e.target.closest('.window-controls') || win.classList.contains('maximized')) return;
    drag = { x: e.clientX, y: e.clientY, left: win.offsetLeft, top: win.offsetTop }; bar.setPointerCapture(e.pointerId);
  });
  bar.addEventListener('pointermove', e => {
    if (!drag) return; win.style.left = `${Math.max(0, drag.left + e.clientX - drag.x)}px`; win.style.top = `${Math.max(0, drag.top + e.clientY - drag.y)}px`;
  });
  bar.addEventListener('pointerup', () => drag = null);
});

function updateClock() {
  const now = new Date(); $('#clockTime').textContent = now.toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' });
  $('#clockDate').textContent = now.toLocaleDateString('pl-PL', { day: '2-digit', month: '2-digit', year: 'numeric' });
}
updateClock(); setInterval(updateClock, 15000);
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeStart(); });
