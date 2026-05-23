/* Menú contextual de Markdown para el editor de /notes/.
 *
 * Se activa con click derecho sobre el área del editor CodeMirror (`window.ED`).
 * Cada opción aplica un cambio al estado del documento vía `ED.view.dispatch()`.
 *
 * Reglas de inserción:
 *   - Wraps inline (negrita, cursiva, código, tachado, link): envuelve el
 *     texto seleccionado; si no hay selección, inserta el marcador con un
 *     placeholder y selecciona el placeholder para que el usuario escriba.
 *   - Prefijos de línea (headings, listas, citas, checklists): prepende el
 *     prefijo a cada línea del rango seleccionado, o a la línea actual si no
 *     hay selección.
 *   - Inserciones de bloque (HR, tabla, bloque de código, salto): inserta el
 *     snippet en la posición del cursor / sustituye la selección.
 */
(function () {
  'use strict';

  const OPTIONS = [
    { key: 'bold',      label: 'Negrita',          kbd: 'Ctrl+B',      kind: 'wrap',    pre: '**', post: '**', placeholder: 'texto' },
    { key: 'italic',    label: 'Cursiva',          kbd: 'Ctrl+I',      kind: 'wrap',    pre: '*',  post: '*',  placeholder: 'texto' },
    { key: 'strike',    label: 'Tachado',          kbd: '',            kind: 'wrap',    pre: '~~', post: '~~', placeholder: 'texto' },
    { key: 'code',      label: 'Código inline',    kbd: 'Ctrl+`',      kind: 'wrap',    pre: '`',  post: '`',  placeholder: 'código' },
    { key: 'link',      label: 'Enlace',           kbd: 'Ctrl+K',      kind: 'link' },
    { sep: true },
    { key: 'h1',        label: 'Encabezado H1',    kbd: '',            kind: 'prefix',  prefix: '# ' },
    { key: 'h2',        label: 'Encabezado H2',    kbd: '',            kind: 'prefix',  prefix: '## ' },
    { key: 'h3',        label: 'Encabezado H3',    kbd: '',            kind: 'prefix',  prefix: '### ' },
    { sep: true },
    { key: 'ul',        label: 'Lista',            kbd: '',            kind: 'prefix',  prefix: '- ' },
    { key: 'ol',        label: 'Lista numerada',   kbd: '',            kind: 'ol' },
    { key: 'check',     label: 'Checklist',        kbd: '',            kind: 'prefix',  prefix: '- [ ] ' },
    { key: 'quote',     label: 'Cita',             kbd: '',            kind: 'prefix',  prefix: '> ' },
    { sep: true },
    { key: 'codeblock', label: 'Bloque de código', kbd: '',            kind: 'block',   snippet: '```\ncódigo\n```' },
    { key: 'table',     label: 'Tabla',            kbd: '',            kind: 'block',   snippet: '| Columna 1 | Columna 2 |\n| --- | --- |\n| celda | celda |\n| celda | celda |' },
    { key: 'hr',        label: 'Línea separadora', kbd: '',            kind: 'block',   snippet: '\n---\n' },
  ];

  let menu = null;       // div del menú
  let isOpen = false;
  let lastSelection = null; // backup de la selección al abrir (CM puede perderla al hacer clic en otra DOM)

  // ── Helpers de edición CodeMirror ────────────────────────────────────────
  function getView() { return window.ED && window.ED.view; }

  function applyWrap(view, pre, post, placeholder) {
    const st = view.state;
    const sel = lastSelection || st.selection.main;
    const text = st.sliceDoc(sel.from, sel.to);
    const inner = text || placeholder || '';
    const insert = pre + inner + post;
    const start = sel.from + pre.length;
    const end = start + inner.length;
    view.dispatch({
      changes: { from: sel.from, to: sel.to, insert },
      selection: { anchor: start, head: end },
      scrollIntoView: true,
    });
    view.focus();
  }

  function applyPrefix(view, prefix) {
    const st = view.state;
    const sel = lastSelection || st.selection.main;
    const startLine = st.doc.lineAt(sel.from);
    const endLine = st.doc.lineAt(sel.to);
    const changes = [];
    for (let n = startLine.number; n <= endLine.number; n++) {
      const ln = st.doc.line(n);
      changes.push({ from: ln.from, to: ln.from, insert: prefix });
    }
    view.dispatch({ changes, scrollIntoView: true });
    view.focus();
  }

  function applyOrdered(view) {
    const st = view.state;
    const sel = lastSelection || st.selection.main;
    const startLine = st.doc.lineAt(sel.from);
    const endLine = st.doc.lineAt(sel.to);
    const changes = [];
    let i = 1;
    for (let n = startLine.number; n <= endLine.number; n++) {
      const ln = st.doc.line(n);
      changes.push({ from: ln.from, to: ln.from, insert: `${i}. ` });
      i++;
    }
    view.dispatch({ changes, scrollIntoView: true });
    view.focus();
  }

  function applyBlock(view, snippet) {
    const st = view.state;
    const sel = lastSelection || st.selection.main;
    // Si estamos a mitad de línea, separamos con \n antes.
    const before = sel.from > 0 ? st.sliceDoc(sel.from - 1, sel.from) : '\n';
    const prefix = before === '\n' ? '' : '\n';
    const insert = prefix + snippet + '\n';
    view.dispatch({
      changes: { from: sel.from, to: sel.to, insert },
      selection: { anchor: sel.from + insert.length },
      scrollIntoView: true,
    });
    view.focus();
  }

  function applyLink(view) {
    const st = view.state;
    const sel = lastSelection || st.selection.main;
    const selected = st.sliceDoc(sel.from, sel.to);
    const url = prompt('URL del enlace:', 'https://');
    if (url === null) { view.focus(); return; }
    const label = selected || 'texto';
    const insert = `[${label}](${url})`;
    view.dispatch({
      changes: { from: sel.from, to: sel.to, insert },
      selection: { anchor: sel.from + 1, head: sel.from + 1 + label.length },
      scrollIntoView: true,
    });
    view.focus();
  }

  function runOption(opt) {
    const view = getView(); if (!view) return;
    switch (opt.kind) {
      case 'wrap':   applyWrap(view, opt.pre, opt.post, opt.placeholder); break;
      case 'prefix': applyPrefix(view, opt.prefix); break;
      case 'ol':     applyOrdered(view); break;
      case 'block':  applyBlock(view, opt.snippet); break;
      case 'link':   applyLink(view); break;
    }
  }

  // ── Menú DOM ──────────────────────────────────────────────────────────────
  function buildMenu() {
    if (menu) return menu;
    menu = document.createElement('div');
    menu.className = 'md-ctxmenu hidden';
    menu.setAttribute('role', 'menu');
    let html = '';
    OPTIONS.forEach((opt, idx) => {
      if (opt.sep) { html += '<div class="md-ctxmenu-sep"></div>'; return; }
      html += `<button class="md-ctxmenu-item" role="menuitem" data-idx="${idx}">
        <span>${opt.label}</span>
        ${opt.kbd ? `<kbd>${opt.kbd}</kbd>` : ''}
      </button>`;
    });
    menu.innerHTML = html;
    document.body.appendChild(menu);
    menu.addEventListener('click', e => {
      const btn = e.target.closest('.md-ctxmenu-item');
      if (!btn) return;
      const opt = OPTIONS[parseInt(btn.dataset.idx, 10)];
      closeMenu();
      runOption(opt);
    });
    return menu;
  }

  function openMenu(x, y) {
    const m = buildMenu();
    // Backup de la selección actual: si el usuario hace clic en el menú,
    // el editor pierde el foco y la selección colapsa.
    const view = getView();
    if (view) {
      const s = view.state.selection.main;
      lastSelection = { from: s.from, to: s.to };
    }
    m.classList.remove('hidden');
    // Reposicionar para no salirse del viewport.
    const w = m.offsetWidth, h = m.offsetHeight;
    const vw = window.innerWidth, vh = window.innerHeight;
    const px = Math.min(x, vw - w - 8);
    const py = Math.min(y, vh - h - 8);
    m.style.left = Math.max(8, px) + 'px';
    m.style.top  = Math.max(8, py) + 'px';
    isOpen = true;
  }

  function closeMenu() {
    if (!menu) return;
    menu.classList.add('hidden');
    isOpen = false;
    lastSelection = null;
  }

  // ── Wire up ──────────────────────────────────────────────────────────────
  function attach() {
    if (!window.ED || !window.ED.dom) { setTimeout(attach, 200); return; }
    const dom = window.ED.dom;
    dom.addEventListener('contextmenu', e => {
      e.preventDefault();
      openMenu(e.clientX, e.clientY);
    });
    document.addEventListener('click', e => {
      if (isOpen && !e.target.closest('.md-ctxmenu')) closeMenu();
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && isOpen) { closeMenu(); }
    });
    // Atajos de teclado clásicos (Ctrl+B, Ctrl+I, Ctrl+K, Ctrl+`)
    dom.addEventListener('keydown', e => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const map = { b: 'bold', i: 'italic', k: 'link', '`': 'code' };
      const key = e.key.toLowerCase();
      const optKey = map[key];
      if (!optKey) return;
      const opt = OPTIONS.find(o => o.key === optKey);
      if (!opt) return;
      e.preventDefault();
      runOption(opt);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attach);
  } else {
    attach();
  }
})();
