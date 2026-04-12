# PDF Assistant

A free, easy-to-use desktop app for editing PDFs — no internet connection or subscription required.

## Features

- **Watermark** — Add diagonal tiled text watermarks to all pages
- **Text Annotation** — Click anywhere on a page to place custom text
- **Signature** — Draw a freehand signature or type one directly onto the PDF
- **Password Protection** — Save your PDF with an optional password
- **Burned-in edits** — All changes are permanently embedded into the PDF content stream, not removable annotation layers

## Requirements

Install dependencies with:

```bash
pip install -r requirements.txt
```

## Usage

```bash
python app.py
```

## Tech Stack

- [PyMuPDF](https://pymupdf.readthedocs.io/) — PDF rendering and editing
- [Pillow](https://pillow.readthedocs.io/) — Image processing
- [Tkinter](https://docs.python.org/3/library/tkinter.html) — GUI

Runs fully offline — your files never leave your machine.

