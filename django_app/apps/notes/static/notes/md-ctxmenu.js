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

  // Opciones específicas de tabla: se anteponen al menú estándar cuando el
  // cursor está dentro de una tabla markdown.
  const TABLE_OPTIONS = [
    { key: 'tbl_row_above',  label: 'Insertar fila arriba',     kind: 'table_op', op: 'rowAbove' },
    { key: 'tbl_row_below',  label: 'Insertar fila debajo',     kind: 'table_op', op: 'rowBelow' },
    { key: 'tbl_col_left',   label: 'Insertar columna izq.',    kind: 'table_op', op: 'colLeft' },
    { key: 'tbl_col_right',  label: 'Insertar columna dcha.',   kind: 'table_op', op: 'colRight' },
    { sep: true },
    { key: 'tbl_del_row',    label: 'Eliminar fila',            kind: 'table_op', op: 'delRow',     danger: true },
    { key: 'tbl_del_col',    label: 'Eliminar columna',         kind: 'table_op', op: 'delCol',     danger: true },
    { key: 'tbl_del',        label: 'Eliminar tabla',           kind: 'table_op', op: 'delTable',   danger: true },
    { sep: true },
  ];

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
    { key: 'table',     label: 'Tabla',            kbd: '',            kind: 'block',   snippet: '| Columna 1 | Columna 2 | Columna 3 |\n| --- | --- | --- |\n| celda | celda | celda |\n| celda | celda | celda |' },
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

  // ── Detección / edición de tablas markdown ───────────────────────────────
  const ROW_RE = /^\s*\|.*\|\s*$/;
  const SEP_RE = /^\s*\|(\s*:?-{3,}:?\s*\|)+\s*$/;

  /** Detecta la tabla que contiene la línea `lineNumber`.
   *  Devuelve {startLine, endLine, headerLine, sepLine, rowCount, colCount}
   *  o `null` si no hay tabla válida en esa posición. */
  function detectTable(state, lineNumber) {
    const doc = state.doc;
    const ln = doc.line(lineNumber);
    if (!ROW_RE.test(ln.text)) return null;
    // Subir hasta el inicio de la tabla.
    let start = lineNumber;
    while (start > 1 && ROW_RE.test(doc.line(start - 1).text)) start--;
    // Bajar hasta el final.
    let end = lineNumber;
    while (end < doc.lines && ROW_RE.test(doc.line(end + 1).text)) end++;
    if (end - start < 1) return null;             // necesita header + separador como mínimo
    const headerLine = start;
    const sepLine = start + 1;
    if (!SEP_RE.test(doc.line(sepLine).text)) return null;
    // Número de columnas = celdas del header (sin contar bordes vacíos).
    const headerCells = splitCells(doc.line(headerLine).text);
    const colCount = headerCells.length;
    return {
      startLine: start,
      endLine: end,
      headerLine: headerLine,
      sepLine: sepLine,
      colCount: colCount,
      rowCount: end - start + 1,
    };
  }

  /** Devuelve el índice de columna (0-based) en la posición `pos` para la
   *  línea `lineNumber`. Si está fuera de cualquier celda → 0. */
  function columnAtPos(state, lineNumber, pos) {
    const ln = state.doc.line(lineNumber);
    const relPos = Math.max(0, Math.min(ln.text.length, pos - ln.from));
    const before = ln.text.slice(0, relPos);
    // Cada | abre una nueva celda. La primera | es el borde, así que la
    // primera celda real es entre la 1ª y la 2ª barra.
    const bars = (before.match(/\|/g) || []).length;
    return Math.max(0, bars - 1);
  }

  /** Split de una fila por `|`, descartando el primer y último elemento
   *  (los bordes vacíos antes/después de la primera/última `|`). */
  function splitCells(rowText) {
    const trimmed = rowText.trim();
    // Separamos por | sin perder los strings vacíos del medio.
    const parts = trimmed.split('|');
    // Si empieza con | y termina con |, el primer y último split son '' (los bordes).
    if (parts.length >= 2 && parts[0].trim() === '') parts.shift();
    if (parts.length >= 1 && parts[parts.length - 1].trim() === '') parts.pop();
    return parts;
  }

  // Texto de relleno para celdas/columnas/cabeceras nuevas: ayuda visual al
  // usuario (sabe dónde escribir) y garantiza que la fila siga siendo una fila
  // markdown válida (`|  |  |` con celdas vacías a veces se pliega visualmente).
  const PH_CELL = 'celda';
  const PH_HEADER = 'Columna';

  function buildRow(cells) {
    return '| ' + cells.map(c => (c == null ? '' : String(c)).trim() || PH_CELL).join(' | ') + ' |';
  }

  function buildSepRow(colCount) {
    return '| ' + Array(colCount).fill('---').join(' | ') + ' |';
  }

  /** Fila nueva: todas las celdas con el placeholder de texto. */
  function emptyRow(colCount) {
    return '| ' + Array(colCount).fill(PH_CELL).join(' | ') + ' |';
  }

  /** Cabecera nueva (sólo para `colLeft/colRight`): la celda añadida usa
   *  `Columna N` para que se distinga semánticamente de las del cuerpo. */
  function headerCellLabel(existingHeader) {
    // Sugerir el siguiente número de columna mirando las existentes.
    const m = (existingHeader || []).map(c => /Columna\s+(\d+)/i.exec(String(c)));
    const max = m.reduce((acc, r) => r ? Math.max(acc, parseInt(r[1], 10)) : acc, 0);
    return `${PH_HEADER} ${max + 1}`;
  }

  function tableOp(view, op) {
    const st = view.state;
    const sel = lastSelection || st.selection.main;
    const cursorLineNumber = st.doc.lineAt(sel.from).number;
    const tbl = detectTable(st, cursorLineNumber);
    if (!tbl) return;

    const targetCol = columnAtPos(st, cursorLineNumber, sel.from);

    if (op === 'rowAbove' || op === 'rowBelow') {
      // Reglas:
      //  - Cursor en header/separador: la fila nueva entra como PRIMERA fila
      //    del cuerpo (justo después del separador).
      //  - Cursor en una fila del cuerpo: insertamos arriba/abajo de esa fila.
      const newRow = emptyRow(tbl.colCount);
      const onHeaderOrSep = (cursorLineNumber === tbl.headerLine || cursorLineNumber === tbl.sepLine);
      let from, insert;
      if (onHeaderOrSep) {
        const sepLn = st.doc.line(tbl.sepLine);
        from = sepLn.to;
        insert = '\n' + newRow;
      } else if (op === 'rowAbove') {
        const target = st.doc.line(cursorLineNumber);
        from = target.from;
        insert = newRow + '\n';
      } else {
        // rowBelow
        const target = st.doc.line(cursorLineNumber);
        from = target.to;
        insert = '\n' + newRow;
      }
      view.dispatch({ changes: { from, to: from, insert }, scrollIntoView: true });
      view.focus();
      return;
    }

    if (op === 'colLeft' || op === 'colRight') {
      const col = targetCol;
      const insertIdx = op === 'colLeft' ? col : col + 1;
      const headerCells = splitCells(st.doc.line(tbl.headerLine).text);
      const newHeader = headerCellLabel(headerCells);
      const changes = [];
      for (let n = tbl.startLine; n <= tbl.endLine; n++) {
        const ln = st.doc.line(n);
        if (n === tbl.sepLine) {
          // Separador: regenerado completo con N+1 celdas '---'.
          changes.push({ from: ln.from, to: ln.to, insert: buildSepRow(tbl.colCount + 1) });
        } else if (n === tbl.headerLine) {
          const cells = splitCells(ln.text);
          cells.splice(insertIdx, 0, newHeader);
          changes.push({ from: ln.from, to: ln.to, insert: buildRow(cells) });
        } else {
          const cells = splitCells(ln.text);
          cells.splice(insertIdx, 0, PH_CELL);
          changes.push({ from: ln.from, to: ln.to, insert: buildRow(cells) });
        }
      }
      view.dispatch({ changes, scrollIntoView: true });
      view.focus();
      return;
    }

    if (op === 'delRow') {
      // No se puede eliminar header ni separador (rompería la tabla).
      if (cursorLineNumber === tbl.headerLine || cursorLineNumber === tbl.sepLine) return;
      if (tbl.rowCount <= 3) return;                    // header + sep + última fila → no borrar
      const ln = st.doc.line(cursorLineNumber);
      // Borramos la línea entera incluido su salto.
      let from = ln.from, to = ln.to;
      if (cursorLineNumber < st.doc.lines) to = to + 1;    // incluye '\n'
      else from = Math.max(0, from - 1);                   // o el '\n' previo si es la última
      view.dispatch({ changes: { from, to, insert: '' }, scrollIntoView: true });
      view.focus();
      return;
    }

    if (op === 'delCol') {
      if (tbl.colCount <= 1) return;                    // dejaríamos una tabla degenerada
      const col = targetCol;
      const changes = [];
      for (let n = tbl.startLine; n <= tbl.endLine; n++) {
        const ln = st.doc.line(n);
        if (n === tbl.sepLine) {
          const cells = Array(tbl.colCount).fill('---');
          cells.splice(col, 1);
          changes.push({ from: ln.from, to: ln.to, insert: '| ' + cells.join(' | ') + ' |' });
        } else {
          const cells = splitCells(ln.text);
          if (cells.length > col) cells.splice(col, 1);
          changes.push({ from: ln.from, to: ln.to, insert: buildRow(cells) });
        }
      }
      view.dispatch({ changes, scrollIntoView: true });
      view.focus();
      return;
    }

    if (op === 'delTable') {
      if (!confirm('¿Eliminar toda la tabla?')) return;
      const fromLine = st.doc.line(tbl.startLine);
      const toLine = st.doc.line(tbl.endLine);
      let from = fromLine.from, to = toLine.to;
      if (toLine.number < st.doc.lines) to = to + 1;
      else from = Math.max(0, from - 1);
      view.dispatch({ changes: { from, to, insert: '' }, scrollIntoView: true });
      view.focus();
      return;
    }
  }

  function runOption(opt) {
    const view = getView(); if (!view) return;
    switch (opt.kind) {
      case 'wrap':     applyWrap(view, opt.pre, opt.post, opt.placeholder); break;
      case 'prefix':   applyPrefix(view, opt.prefix); break;
      case 'ol':       applyOrdered(view); break;
      case 'block':    applyBlock(view, opt.snippet); break;
      case 'link':     applyLink(view); break;
      case 'table_op': tableOp(view, opt.op); break;
    }
  }

  // ── Menú DOM ──────────────────────────────────────────────────────────────
  /** Construye (o reconstruye) el menú con la lista de opciones dada. */
  function renderMenu(opts) {
    if (!menu) {
      menu = document.createElement('div');
      menu.className = 'md-ctxmenu hidden';
      menu.setAttribute('role', 'menu');
      document.body.appendChild(menu);
      menu.addEventListener('click', e => {
        const btn = e.target.closest('.md-ctxmenu-item');
        if (!btn) return;
        const opt = menu._opts[parseInt(btn.dataset.idx, 10)];
        closeMenu();
        runOption(opt);
      });
    }
    menu._opts = opts;
    let html = '';
    opts.forEach((opt, idx) => {
      if (opt.sep) { html += '<div class="md-ctxmenu-sep"></div>'; return; }
      const cls = 'md-ctxmenu-item' + (opt.danger ? ' md-ctxmenu-danger' : '');
      html += `<button class="${cls}" role="menuitem" data-idx="${idx}">
        <span>${opt.label}</span>
        ${opt.kbd ? `<kbd>${opt.kbd}</kbd>` : ''}
      </button>`;
    });
    menu.innerHTML = html;
    return menu;
  }

  function openMenu(x, y) {
    // Backup de la selección actual: si el usuario hace clic en el menú,
    // el editor pierde el foco y la selección colapsa.
    const view = getView();
    if (view) {
      const s = view.state.selection.main;
      lastSelection = { from: s.from, to: s.to };
    }
    // Si el cursor está en una tabla, ofrecemos primero las ops de tabla.
    let opts = OPTIONS.slice();
    if (view) {
      const lineN = view.state.doc.lineAt(view.state.selection.main.from).number;
      if (detectTable(view.state, lineN)) {
        opts = TABLE_OPTIONS.concat(opts);
      }
    }
    const m = renderMenu(opts);
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
      // Mueve el cursor al punto del click antes de abrir el menú, para que
      // las ops de tabla (insertar fila/columna) operen en la celda clicada.
      const view = getView();
      if (view && typeof view.posAtCoords === 'function') {
        const pos = view.posAtCoords({ x: e.clientX, y: e.clientY });
        if (pos != null) {
          view.dispatch({ selection: { anchor: pos } });
        }
      }
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
