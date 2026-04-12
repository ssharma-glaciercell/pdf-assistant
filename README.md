# PDF Assistant

A free, easy-to-use desktop app for editing PDFs — no internet connection or subscription required.

## Features

- **View PDFs** – smooth rendering with zoom and page navigation
- **Add text annotations** – click anywhere on a page to add text in any colour and size
- **Highlight content** – drag to highlight rectangular areas
- **Rotate pages** – rotate any page 90° clockwise
- **Delete pages** – remove unwanted pages from a document
- **Merge PDFs** – append another PDF file to the current document
- **Save / Save As** – save changes back to the original file or to a new location
- **Fully offline** – no internet connection needed, ever
- **Free** – no subscription, no account, no telemetry

## Getting Started

### Prerequisites

- [Node.js](https://nodejs.org/) 18 or later

### Install

```bash
npm install
```

### Run

```bash
npm start
```

### Build distributable

```bash
npm run build
```

Installers are written to the `dist/` folder (AppImage on Linux, DMG on macOS, NSIS installer on Windows).

## Running tests

```bash
npm test
```

Tests exercise core PDF operations (create, annotate, rotate, delete pages, merge) using **pdf-lib** directly, without requiring a display or Electron.

## Tech stack

| Library | Purpose |
|---------|---------|
| [Electron](https://www.electronjs.org/) | Cross-platform desktop shell |
| [PDF.js](https://mozilla.github.io/pdf.js/) | PDF rendering in the viewer |
| [pdf-lib](https://pdf-lib.js.org/) | PDF editing and saving |

## Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl/Cmd + O` | Open PDF |
| `Ctrl/Cmd + S` | Save |
| `Ctrl/Cmd + Shift + S` | Save As |
| `←` / `→` | Previous / next page |
| `Escape` | Cancel current tool |

## License

ISC
