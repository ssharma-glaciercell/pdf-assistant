/**
 * PDF Assistant – renderer process (ES module)
 * Uses PDF.js for rendering and pdf-lib for editing/saving.
 */

import { getDocument, GlobalWorkerOptions } from '../node_modules/pdfjs-dist/build/pdf.mjs';

// Point PDF.js worker to the bundled worker
GlobalWorkerOptions.workerSrc = '../node_modules/pdfjs-dist/build/pdf.worker.mjs';

// ── State ──────────────────────────────────────────────────────────────────

const state = {
  pdfDoc: null,           // PDFDocumentProxy (PDF.js)
  pdfBytes: null,         // Uint8Array of current PDF bytes
  currentPage: 1,
  totalPages: 0,
  scale: 1.5,
  currentFilePath: null,
  dirty: false,
  highlightMode: false,
  pendingTextClick: false,
  // per-page annotation data: Map<pageNum, annotation[]>
  annotations: new Map(),
  // rotation overrides per page (degrees, multiples of 90)
  rotations: new Map(),
};

// ── DOM refs ──────────────────────────────────────────────────────────────

const els = {
  btnOpen: document.getElementById('btn-open'),
  btnSave: document.getElementById('btn-save'),
  btnSaveAs: document.getElementById('btn-save-as'),
  btnAddText: document.getElementById('btn-add-text'),
  btnHighlight: document.getElementById('btn-highlight'),
  btnRotateCw: document.getElementById('btn-rotate-cw'),
  btnDeletePage: document.getElementById('btn-delete-page'),
  btnMerge: document.getElementById('btn-merge'),
  btnPrev: document.getElementById('btn-prev'),
  btnNext: document.getElementById('btn-next'),
  btnZoomIn: document.getElementById('btn-zoom-in'),
  btnZoomOut: document.getElementById('btn-zoom-out'),
  btnZoomFit: document.getElementById('btn-zoom-fit'),
  pageInfo: document.getElementById('page-info'),
  zoomLevel: document.getElementById('zoom-level'),
  filenameDisplay: document.getElementById('filename-display'),
  thumbnailList: document.getElementById('thumbnail-list'),
  viewerContainer: document.getElementById('viewer-container'),
  canvasWrapper: document.getElementById('canvas-wrapper'),
  pdfCanvas: document.getElementById('pdf-canvas'),
  annotationLayer: document.getElementById('annotation-layer'),
  welcomeScreen: document.getElementById('welcome-screen'),
  textPopover: document.getElementById('text-popover'),
  textInput: document.getElementById('text-input'),
  fontSize: document.getElementById('font-size'),
  fontColor: document.getElementById('font-color'),
  textConfirm: document.getElementById('text-confirm'),
  textCancel: document.getElementById('text-cancel'),
  statusMsg: document.getElementById('status-msg'),
  btnWelcomeOpen: document.getElementById('btn-welcome-open'),
};

// ── Helpers ───────────────────────────────────────────────────────────────

function setStatus(msg) {
  els.statusMsg.textContent = msg;
}

function markDirty() {
  state.dirty = true;
  document.title = `PDF Assistant – ${state.currentFilePath ? basename(state.currentFilePath) + ' *' : 'Untitled *'}`;
}

function clearDirty() {
  state.dirty = false;
  document.title = `PDF Assistant – ${state.currentFilePath ? basename(state.currentFilePath) : 'Untitled'}`;
}

function basename(filePath) {
  return filePath.replace(/.*[\\/]/, '');
}

function setEditorEnabled(enabled) {
  els.btnSave.disabled = !enabled;
  els.btnSaveAs.disabled = !enabled;
  els.btnAddText.disabled = !enabled;
  els.btnHighlight.disabled = !enabled;
  els.btnRotateCw.disabled = !enabled;
  els.btnDeletePage.disabled = !enabled;
  els.btnMerge.disabled = !enabled;
  els.btnPrev.disabled = !enabled;
  els.btnNext.disabled = !enabled;
  els.btnZoomIn.disabled = !enabled;
  els.btnZoomOut.disabled = !enabled;
  els.btnZoomFit.disabled = !enabled;
}

// ── Open PDF ──────────────────────────────────────────────────────────────

async function openPdf() {
  setStatus('Opening…');
  const result = await window.electronAPI.openFile();
  if (!result) {
    setStatus('Ready');
    return;
  }
  await loadPdfBytes(new Uint8Array(result.data), result.filePath);
}

async function loadPdfBytes(bytes, filePath) {
  try {
    state.pdfBytes = bytes;
    state.currentFilePath = filePath || null;
    state.annotations.clear();
    state.rotations.clear();

    const loadingTask = getDocument({ data: bytes });
    state.pdfDoc = await loadingTask.promise;
    state.totalPages = state.pdfDoc.numPages;
    state.currentPage = 1;

    els.welcomeScreen.style.display = 'none';
    els.canvasWrapper.style.display = 'block';

    setEditorEnabled(true);
    clearDirty();

    const name = filePath ? basename(filePath) : 'Untitled';
    els.filenameDisplay.textContent = name;
    document.title = `PDF Assistant – ${name}`;

    await renderThumbnails();
    await renderPage(state.currentPage);
    setStatus(`Opened: ${name}`);
  } catch (err) {
    setStatus(`Error opening PDF: ${err.message}`);
    console.error(err);
  }
}

// ── Render page ───────────────────────────────────────────────────────────

async function renderPage(pageNum) {
  const page = await state.pdfDoc.getPage(pageNum);
  const extraRotation = state.rotations.get(pageNum) || 0;
  const viewport = page.getViewport({ scale: state.scale, rotation: extraRotation });

  const canvas = els.pdfCanvas;
  const ctx = canvas.getContext('2d');
  const ratio = window.devicePixelRatio || 1;
  canvas.width = viewport.width * ratio;
  canvas.height = viewport.height * ratio;
  canvas.style.width = `${viewport.width}px`;
  canvas.style.height = `${viewport.height}px`;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

  await page.render({ canvasContext: ctx, viewport }).promise;

  // Update annotation layer size
  els.annotationLayer.style.width = `${viewport.width}px`;
  els.annotationLayer.style.height = `${viewport.height}px`;

  // Re-render annotations for this page
  renderAnnotations(pageNum);

  // Update navigation UI
  state.currentPage = pageNum;
  els.pageInfo.textContent = `Page ${pageNum} / ${state.totalPages}`;
  els.btnPrev.disabled = pageNum <= 1;
  els.btnNext.disabled = pageNum >= state.totalPages;
  els.zoomLevel.textContent = `${Math.round(state.scale * 100)}%`;

  // Highlight active thumbnail
  document.querySelectorAll('.thumb-item').forEach((el) => {
    el.classList.toggle('active', parseInt(el.dataset.page, 10) === pageNum);
  });
}

// ── Thumbnails ────────────────────────────────────────────────────────────

async function renderThumbnails() {
  els.thumbnailList.innerHTML = '';
  for (let i = 1; i <= state.totalPages; i++) {
    const page = await state.pdfDoc.getPage(i);
    const viewport = page.getViewport({ scale: 0.2 });

    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    await page.render({ canvasContext: ctx, viewport }).promise;

    const item = document.createElement('div');
    item.className = 'thumb-item';
    item.dataset.page = i;
    item.appendChild(canvas);
    const label = document.createElement('div');
    label.className = 'thumb-label';
    label.textContent = i;
    item.appendChild(label);

    item.addEventListener('click', () => renderPage(i));
    els.thumbnailList.appendChild(item);
  }
}

// ── Annotations ───────────────────────────────────────────────────────────

function getAnnotations(pageNum) {
  if (!state.annotations.has(pageNum)) state.annotations.set(pageNum, []);
  return state.annotations.get(pageNum);
}

function renderAnnotations(pageNum) {
  els.annotationLayer.innerHTML = '';
  const list = getAnnotations(pageNum);
  list.forEach((ann, idx) => {
    const el = createAnnotationElement(ann, idx, pageNum);
    els.annotationLayer.appendChild(el);
  });
}

function createAnnotationElement(ann, idx, pageNum) {
  const el = document.createElement('div');
  el.className = `annotation-item ${ann.type}-annotation`;
  el.style.left = `${ann.x}px`;
  el.style.top = `${ann.y}px`;

  if (ann.type === 'text') {
    el.style.fontSize = `${ann.fontSize}px`;
    el.style.color = ann.color;
    el.textContent = ann.text;
    if (ann.width) el.style.minWidth = `${ann.width}px`;
  } else if (ann.type === 'highlight') {
    el.style.width = `${ann.width}px`;
    el.style.height = `${ann.height}px`;
  }

  // Delete button
  const del = document.createElement('button');
  del.className = 'annotation-delete';
  del.textContent = '×';
  del.title = 'Remove annotation';
  del.addEventListener('click', (e) => {
    e.stopPropagation();
    getAnnotations(pageNum).splice(idx, 1);
    renderAnnotations(pageNum);
    markDirty();
  });
  el.appendChild(del);

  // Dragging
  makeDraggable(el, ann, pageNum);

  return el;
}

function makeDraggable(el, ann, pageNum) {
  let startX, startY, startAnnX, startAnnY;

  el.addEventListener('mousedown', (e) => {
    if (e.target.classList.contains('annotation-delete')) return;
    e.preventDefault();
    startX = e.clientX;
    startY = e.clientY;
    startAnnX = ann.x;
    startAnnY = ann.y;

    const onMove = (ev) => {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      ann.x = startAnnX + dx;
      ann.y = startAnnY + dy;
      el.style.left = `${ann.x}px`;
      el.style.top = `${ann.y}px`;
    };

    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      markDirty();
      renderAnnotations(pageNum);
    };

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}

// ── Text annotation ───────────────────────────────────────────────────────

function enterTextMode() {
  state.pendingTextClick = true;
  els.canvasWrapper.style.cursor = 'crosshair';
  setStatus('Click on the page where you want to add text');
}

function showTextPopover(x, y) {
  const popover = els.textPopover;
  popover.style.display = 'block';
  popover.style.left = `${x + 4}px`;
  popover.style.top = `${y + 4}px`;
  els.textInput.value = '';
  els.textInput.focus();
  state.pendingTextClick = false;
  els.canvasWrapper.style.cursor = 'default';
  popover._clickX = x;
  popover._clickY = y;
}

els.textConfirm.addEventListener('click', () => {
  const text = els.textInput.value.trim();
  if (!text) return;
  const x = els.textPopover._clickX;
  const y = els.textPopover._clickY;
  const fontSize = parseInt(els.fontSize.value, 10) || 14;
  const color = els.fontColor.value;

  getAnnotations(state.currentPage).push({ type: 'text', x, y, text, fontSize, color });
  renderAnnotations(state.currentPage);
  els.textPopover.style.display = 'none';
  markDirty();
  setStatus('Text annotation added');
});

els.textCancel.addEventListener('click', () => {
  els.textPopover.style.display = 'none';
  setStatus('Ready');
});

// ── Highlight mode ────────────────────────────────────────────────────────

let highlightStart = null;

function toggleHighlightMode() {
  state.highlightMode = !state.highlightMode;
  els.btnHighlight.classList.toggle('active', state.highlightMode);
  els.canvasWrapper.style.cursor = state.highlightMode ? 'crosshair' : 'default';
  setStatus(state.highlightMode ? 'Drag to highlight an area' : 'Ready');
}

// ── Canvas click / drag (text + highlight) ────────────────────────────────

els.canvasWrapper.addEventListener('mousedown', (e) => {
  if (e.target === els.textPopover || els.textPopover.contains(e.target)) return;

  const rect = els.pdfCanvas.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;

  if (state.pendingTextClick) {
    showTextPopover(x, y);
    return;
  }

  if (state.highlightMode) {
    highlightStart = { x, y };
  }
});

els.canvasWrapper.addEventListener('mouseup', (e) => {
  if (!state.highlightMode || !highlightStart) return;

  const rect = els.pdfCanvas.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;

  const startX = Math.min(highlightStart.x, x);
  const startY = Math.min(highlightStart.y, y);
  const w = Math.abs(x - highlightStart.x);
  const h = Math.abs(y - highlightStart.y);
  highlightStart = null;

  if (w < 5 || h < 5) return; // too small

  getAnnotations(state.currentPage).push({
    type: 'highlight',
    x: startX,
    y: startY,
    width: w,
    height: h,
  });
  renderAnnotations(state.currentPage);
  markDirty();
  setStatus('Highlight added');
});

// ── Rotate page ───────────────────────────────────────────────────────────

async function rotateCurrentPage() {
  const current = state.rotations.get(state.currentPage) || 0;
  state.rotations.set(state.currentPage, (current + 90) % 360);
  await renderPage(state.currentPage);
  markDirty();
  setStatus(`Page ${state.currentPage} rotated`);
}

// ── Delete page ───────────────────────────────────────────────────────────

async function deleteCurrentPage() {
  if (state.totalPages <= 1) {
    setStatus('Cannot delete the only page');
    return;
  }

  // Rebuild PDF bytes without the current page using pdf-lib
  const { PDFDocument } = await import('../node_modules/pdf-lib/es/index.js');
  const srcDoc = await PDFDocument.load(state.pdfBytes);
  const newDoc = await PDFDocument.create();
  const pageCount = srcDoc.getPageCount();
  const indices = [];
  for (let i = 0; i < pageCount; i++) {
    if (i !== state.currentPage - 1) indices.push(i);
  }
  const pages = await newDoc.copyPages(srcDoc, indices);
  pages.forEach((p) => newDoc.addPage(p));
  const newBytes = await newDoc.save();

  // Reload with new bytes, keeping current file path
  const savedPath = state.currentFilePath;
  await loadPdfBytes(newBytes, savedPath);
  markDirty();

  const targetPage = Math.min(state.currentPage, state.totalPages);
  await renderPage(targetPage);
  setStatus(`Page deleted`);
}

// ── Merge PDF ─────────────────────────────────────────────────────────────

async function mergePdf() {
  setStatus('Select a PDF to merge…');
  const result = await window.electronAPI.openFile();
  if (!result) {
    setStatus('Ready');
    return;
  }

  const { PDFDocument } = await import('../node_modules/pdf-lib/es/index.js');
  const baseDoc = await PDFDocument.load(state.pdfBytes);
  const appendDoc = await PDFDocument.load(new Uint8Array(result.data));
  const count = appendDoc.getPageCount();
  const indices = Array.from({ length: count }, (_, i) => i);
  const pages = await baseDoc.copyPages(appendDoc, indices);
  pages.forEach((p) => baseDoc.addPage(p));
  const newBytes = await baseDoc.save();

  const savedPath = state.currentFilePath;
  await loadPdfBytes(newBytes, savedPath);
  markDirty();
  setStatus(`Merged ${count} page(s) from ${basename(result.filePath)}`);
}

// ── Save ──────────────────────────────────────────────────────────────────

async function savePdf(saveAs = false) {
  let filePath = state.currentFilePath;
  if (saveAs || !filePath) {
    filePath = await window.electronAPI.saveFile(filePath || 'document.pdf');
    if (!filePath) return;
  }

  try {
    setStatus('Saving…');
    const bytes = await buildFinalPdf();
    await window.electronAPI.writeFile(filePath, bytes.buffer);
    state.currentFilePath = filePath;
    state.pdfBytes = bytes;
    clearDirty();
    els.filenameDisplay.textContent = basename(filePath);
    setStatus(`Saved: ${basename(filePath)}`);
  } catch (err) {
    setStatus(`Save failed: ${err.message}`);
    console.error(err);
  }
}

/**
 * Flatten annotations into the PDF bytes using pdf-lib, then apply rotations.
 */
async function buildFinalPdf() {
  const { PDFDocument, rgb, StandardFonts } = await import('../node_modules/pdf-lib/es/index.js');
  const pdfDoc = await PDFDocument.load(state.pdfBytes);
  const helvetica = await pdfDoc.embedFont(StandardFonts.Helvetica);
  const pages = pdfDoc.getPages();

  for (let i = 0; i < pages.length; i++) {
    const pageNum = i + 1;
    const page = pages[i];
    const { width, height } = page.getSize();

    // Apply rotation
    const rot = state.rotations.get(pageNum) || 0;
    if (rot) {
      const existing = page.getRotation().angle;
      page.setRotation({ type: 'degrees', angle: (existing + rot) % 360 });
    }

    // Flatten annotations
    const anns = getAnnotations(pageNum);
    const canvasEl = els.pdfCanvas;
    const canvasWidth = canvasEl.offsetWidth || canvasEl.width;
    const canvasHeight = canvasEl.offsetHeight || canvasEl.height;

    for (const ann of anns) {
      // Convert canvas pixel coords → PDF points
      const scaleX = width / canvasWidth;
      const scaleY = height / canvasHeight;
      const pdfX = ann.x * scaleX;
      // PDF coords: origin bottom-left; canvas: top-left
      const pdfY = height - ann.y * scaleY;

      if (ann.type === 'text') {
        const hexColor = ann.color || '#000000';
        const r = parseInt(hexColor.slice(1, 3), 16) / 255;
        const g = parseInt(hexColor.slice(3, 5), 16) / 255;
        const b = parseInt(hexColor.slice(5, 7), 16) / 255;
        const fontSize = (ann.fontSize || 14) * scaleY;
        page.drawText(ann.text, {
          x: pdfX,
          y: pdfY - fontSize,
          size: fontSize,
          font: helvetica,
          color: rgb(r, g, b),
        });
      } else if (ann.type === 'highlight') {
        const annWidth = ann.width * scaleX;
        const annHeight = ann.height * scaleY;
        page.drawRectangle({
          x: pdfX,
          y: pdfY - annHeight,
          width: annWidth,
          height: annHeight,
          color: rgb(1, 0.9, 0.4),
          opacity: 0.4,
        });
      }
    }
  }

  return await pdfDoc.save();
}

// ── Navigation ────────────────────────────────────────────────────────────

els.btnPrev.addEventListener('click', () => {
  if (state.currentPage > 1) renderPage(state.currentPage - 1);
});

els.btnNext.addEventListener('click', () => {
  if (state.currentPage < state.totalPages) renderPage(state.currentPage + 1);
});

els.btnZoomIn.addEventListener('click', async () => {
  state.scale = Math.min(state.scale + 0.25, 4);
  await renderPage(state.currentPage);
});

els.btnZoomOut.addEventListener('click', async () => {
  state.scale = Math.max(state.scale - 0.25, 0.5);
  await renderPage(state.currentPage);
});

els.btnZoomFit.addEventListener('click', async () => {
  if (!state.pdfDoc) return;
  const page = await state.pdfDoc.getPage(state.currentPage);
  const naturalVP = page.getViewport({ scale: 1 });
  const containerWidth = els.viewerContainer.clientWidth - 40;
  state.scale = containerWidth / naturalVP.width;
  await renderPage(state.currentPage);
});

// Keyboard navigation
window.addEventListener('keydown', (e) => {
  if (!state.pdfDoc) return;
  if (['ArrowRight', 'ArrowDown'].includes(e.key)) {
    if (state.currentPage < state.totalPages) renderPage(state.currentPage + 1);
  } else if (['ArrowLeft', 'ArrowUp'].includes(e.key)) {
    if (state.currentPage > 1) renderPage(state.currentPage - 1);
  } else if (e.key === 'Escape') {
    state.pendingTextClick = false;
    state.highlightMode = false;
    els.btnHighlight.classList.remove('active');
    els.canvasWrapper.style.cursor = 'default';
    els.textPopover.style.display = 'none';
    setStatus('Ready');
  }
});

// ── Button wiring ─────────────────────────────────────────────────────────

els.btnOpen.addEventListener('click', openPdf);
els.btnWelcomeOpen.addEventListener('click', openPdf);
els.btnSave.addEventListener('click', () => savePdf(false));
els.btnSaveAs.addEventListener('click', () => savePdf(true));
els.btnAddText.addEventListener('click', enterTextMode);
els.btnHighlight.addEventListener('click', toggleHighlightMode);
els.btnRotateCw.addEventListener('click', rotateCurrentPage);
els.btnDeletePage.addEventListener('click', deleteCurrentPage);
els.btnMerge.addEventListener('click', mergePdf);

// ── Electron menu integration ──────────────────────────────────────────────

window.electronAPI.onMenuOpen(() => openPdf());
window.electronAPI.onMenuSave(() => savePdf(false));
window.electronAPI.onMenuSaveAs(() => savePdf(true));

setStatus('Ready – open a PDF to get started');
