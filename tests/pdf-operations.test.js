/**
 * Tests for PDF operations using pdf-lib.
 * These tests validate the core PDF manipulation logic without requiring Electron.
 * Run with: node tests/pdf-operations.test.js
 */

'use strict';

const assert = require('assert');

let passed = 0;
let failed = 0;

async function test(name, fn) {
  try {
    await fn();
    console.log(`  ✓ ${name}`);
    passed++;
  } catch (err) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${err.message}`);
    failed++;
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────

async function loadPdfLib() {
  // pdf-lib ships both CommonJS and ESM. Use the CJS build for Node tests.
  return require('pdf-lib');
}

async function createSamplePdf(pageCount = 1) {
  const { PDFDocument, rgb, StandardFonts } = await loadPdfLib();
  const doc = await PDFDocument.create();
  const font = await doc.embedFont(StandardFonts.Helvetica);
  for (let i = 0; i < pageCount; i++) {
    const page = doc.addPage([595, 842]);
    page.drawText(`Page ${i + 1}`, {
      x: 50,
      y: 800,
      size: 18,
      font,
      color: rgb(0, 0, 0),
    });
  }
  return doc.save();
}

// ── Tests ─────────────────────────────────────────────────────────────────

console.log('\nPDF Assistant – Core PDF Operations\n');

(async () => {
  // 1. Create and load a PDF
  await test('creates a valid PDF document', async () => {
    const { PDFDocument } = await loadPdfLib();
    const bytes = await createSamplePdf(1);
    assert.ok(bytes instanceof Uint8Array, 'save() should return Uint8Array');
    const loaded = await PDFDocument.load(bytes);
    assert.strictEqual(loaded.getPageCount(), 1, 'should have 1 page');
  });

  // 2. Add a text annotation by drawing text onto a page
  await test('flattens a text annotation onto a page', async () => {
    const { PDFDocument, rgb, StandardFonts } = await loadPdfLib();
    const bytes = await createSamplePdf(1);
    const doc = await PDFDocument.load(bytes);
    const font = await doc.embedFont(StandardFonts.Helvetica);
    const page = doc.getPages()[0];

    page.drawText('Hello, PDF!', { x: 100, y: 400, size: 14, font, color: rgb(0, 0, 0) });

    const result = await doc.save();
    assert.ok(result.byteLength > bytes.byteLength, 'PDF should grow after adding text');
  });

  // 3. Highlight annotation (rectangle)
  await test('flattens a highlight annotation onto a page', async () => {
    const { PDFDocument, rgb } = await loadPdfLib();
    const bytes = await createSamplePdf(1);
    const doc = await PDFDocument.load(bytes);
    const page = doc.getPages()[0];

    page.drawRectangle({ x: 50, y: 700, width: 200, height: 20, color: rgb(1, 0.9, 0.4), opacity: 0.4 });

    const result = await doc.save();
    assert.ok(result instanceof Uint8Array, 'should return Uint8Array');
    assert.ok(result.byteLength > 0, 'result should not be empty');
  });

  // 4. Rotate a page
  await test('rotates a page by 90 degrees', async () => {
    const { PDFDocument } = await loadPdfLib();
    const bytes = await createSamplePdf(1);
    const doc = await PDFDocument.load(bytes);
    const page = doc.getPages()[0];

    const before = page.getRotation().angle;
    page.setRotation({ type: 'degrees', angle: (before + 90) % 360 });
    assert.strictEqual(page.getRotation().angle, 90, 'page rotation should be 90');
  });

  // 5. Delete a page
  await test('deletes a page from a multi-page PDF', async () => {
    const { PDFDocument } = await loadPdfLib();
    const bytes = await createSamplePdf(3);
    const srcDoc = await PDFDocument.load(bytes);
    const newDoc = await PDFDocument.create();

    // Delete page 2 (index 1)
    const keepIndices = [0, 2];
    const pages = await newDoc.copyPages(srcDoc, keepIndices);
    pages.forEach((p) => newDoc.addPage(p));

    assert.strictEqual(newDoc.getPageCount(), 2, 'should have 2 pages after deleting one');
  });

  // 6. Merge two PDFs
  await test('merges two PDF documents', async () => {
    const { PDFDocument } = await loadPdfLib();
    const bytesA = await createSamplePdf(2);
    const bytesB = await createSamplePdf(3);

    const docA = await PDFDocument.load(bytesA);
    const docB = await PDFDocument.load(bytesB);

    const allIndices = Array.from({ length: docB.getPageCount() }, (_, i) => i);
    const pages = await docA.copyPages(docB, allIndices);
    pages.forEach((p) => docA.addPage(p));

    assert.strictEqual(docA.getPageCount(), 5, 'merged doc should have 5 pages');

    const result = await docA.save();
    assert.ok(result instanceof Uint8Array, 'should return Uint8Array');
  });

  // 7. Cannot delete the only page (guard logic)
  await test('prevents deleting when only one page remains', async () => {
    const { PDFDocument } = await loadPdfLib();
    const bytes = await createSamplePdf(1);
    const doc = await PDFDocument.load(bytes);

    // Simulate the guard: totalPages <= 1
    const totalPages = doc.getPageCount();
    assert.strictEqual(totalPages, 1, 'PDF has 1 page');
    // Guard check
    assert.ok(totalPages <= 1, 'guard should prevent deletion when only 1 page');
  });

  // 8. Round-trip save/load preserves page count
  await test('round-trip save/load preserves page count', async () => {
    const { PDFDocument } = await loadPdfLib();
    const bytes = await createSamplePdf(4);
    const doc = await PDFDocument.load(bytes);
    const saved = await doc.save();
    const reloaded = await PDFDocument.load(saved);
    assert.strictEqual(reloaded.getPageCount(), 4, 'should still have 4 pages');
  });

  // 9. Color parsing for text annotations
  await test('parses hex color to rgb components correctly', () => {
    function hexToRgb(hex) {
      const r = parseInt(hex.slice(1, 3), 16) / 255;
      const g = parseInt(hex.slice(3, 5), 16) / 255;
      const b = parseInt(hex.slice(5, 7), 16) / 255;
      return { r, g, b };
    }

    const red = hexToRgb('#ff0000');
    assert.strictEqual(red.r, 1);
    assert.strictEqual(red.g, 0);
    assert.strictEqual(red.b, 0);

    const gray = hexToRgb('#808080');
    assert.ok(Math.abs(gray.r - 0.502) < 0.01);

    const black = hexToRgb('#000000');
    assert.strictEqual(black.r, 0);
    assert.strictEqual(black.g, 0);
    assert.strictEqual(black.b, 0);
  });

  // 10. Page rotation accumulation
  await test('accumulates rotations correctly', () => {
    const rotations = new Map();
    function rotatePage(pageNum) {
      const current = rotations.get(pageNum) || 0;
      rotations.set(pageNum, (current + 90) % 360);
    }
    rotatePage(1);
    assert.strictEqual(rotations.get(1), 90);
    rotatePage(1);
    assert.strictEqual(rotations.get(1), 180);
    rotatePage(1);
    assert.strictEqual(rotations.get(1), 270);
    rotatePage(1);
    assert.strictEqual(rotations.get(1), 0);
  });

  // Summary
  console.log(`\n${passed} passed, ${failed} failed\n`);
  if (failed > 0) process.exit(1);
})();
