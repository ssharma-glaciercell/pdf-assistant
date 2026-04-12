"""
PDF Engine — core operations: watermark, signature, text annotation, and save.

All modifications are "burned in" (flattened) so they cannot be stripped by
a simple PDF editor.  The strategy is:

1. Open the PDF with PyMuPDF.
2. Apply every annotation (watermark / text / signature) by drawing directly
   into the page's content stream using page.insert_* methods so the marks
   become part of the page content, not removable annotation objects.
3. Re-save with garbage=4 (remove unreferenced objects), deflate=True
   (compress streams) and no_new_id=False so the file ID changes.  We also
   call doc.save() with encryption=fitz.PDF_ENCRYPT_KEEP so existing
   permissions survive, and optionally allow the caller to add a new owner
   password that forbids editing.
"""

from __future__ import annotations

import math
import io
from pathlib import Path
from typing import Optional, Tuple

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pil_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDFDocument — thin wrapper around fitz.Document
# ---------------------------------------------------------------------------

class PDFDocument:
    """Wrapper around a PyMuPDF document with high-level editing methods."""

    def __init__(self) -> None:
        self._doc: Optional[fitz.Document] = None
        self._path: Optional[Path] = None
        # pending annotations per page: list of callables(page) -> None
        # These are applied lazily just before saving so the GUI can preview
        # without committing.
        self._pending: list[tuple[int, object]] = []

    # ------------------------------------------------------------------
    # Open / close
    # ------------------------------------------------------------------

    def open(self, path: str | Path) -> int:
        """Open a PDF file.  Returns number of pages."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        self._doc = fitz.open(str(path))
        self._path = path
        self._pending.clear()
        return len(self._doc)

    def close(self) -> None:
        if self._doc:
            self._doc.close()
            self._doc = None
            self._path = None
            self._pending.clear()

    @property
    def page_count(self) -> int:
        return len(self._doc) if self._doc else 0

    # ------------------------------------------------------------------
    # Render a page to PIL Image (for preview)
    # ------------------------------------------------------------------

    def render_page(self, page_index: int, zoom: float = 1.5) -> Image.Image:
        """Render page to a PIL Image at the given zoom level."""
        page = self._doc[page_index]
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    # ------------------------------------------------------------------
    # Watermark  (burned directly into content stream)
    # ------------------------------------------------------------------

    def apply_watermark(
        self,
        text: str,
        opacity: float = 0.75,
        font_size: int = 80,
        color: Tuple[float, float, float] = (0.8, 0.0, 0.0),
        angle: float = 45.0,
        progress_callback=None,
    ) -> None:
        """
        Burn a diagonal tiled text watermark on EVERY page using a PIL RGBA
        image overlay so that `opacity` is a true alpha value (0 = invisible,
        1 = fully opaque).  The image is inserted directly into the page
        content — it is NOT a removable annotation object.

        progress_callback(current: int, total: int) is called after each page.
        """
        if not self._doc:
            raise RuntimeError("No document open.")

        r_int = int(color[0] * 255)
        g_int = int(color[1] * 255)
        b_int = int(color[2] * 255)
        a_int = max(0, min(255, int(opacity * 255)))
        fill = (r_int, g_int, b_int, a_int)

        # Scale factor: render the overlay at 2× resolution for crisp text
        scale = 2
        font_px = max(8, int(font_size * scale))

        # Try to load a system font; fall back to PIL default
        pil_font: ImageFont.FreeTypeFont | ImageFont.ImageFont
        _font_candidates = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
        pil_font = ImageFont.load_default()
        for candidate in _font_candidates:
            try:
                pil_font = ImageFont.truetype(candidate, font_px)
                break
            except (OSError, IOError):
                continue

        # Pre-render the rotated tile ONCE — reused for every position/page
        # Use a large fixed scratch surface for measurement
        scratch = Image.new("RGBA", (8000, 4000))
        scratch_draw = ImageDraw.Draw(scratch)
        bbox = scratch_draw.multiline_textbbox(
            (0, 0), text, font=pil_font, spacing=8
        )
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        padding = int(font_px * 1.5)
        side = int(math.hypot(text_w, text_h)) + padding * 2

        tile = Image.new("RGBA", (side, side), (255, 255, 255, 0))
        tile_draw = ImageDraw.Draw(tile)
        tx = (side - text_w) // 2 - bbox[0]
        ty = (side - text_h) // 2 - bbox[1]
        tile_draw.multiline_text(
            (tx, ty), text, font=pil_font,
            fill=fill, spacing=8, align="center"
        )
        rotated = tile.rotate(angle, expand=False, resample=Image.BICUBIC)
        stamp_w, stamp_h = rotated.size

        for page_num, page in enumerate(self._doc):
            w_pt = page.rect.width
            h_pt = page.rect.height
            w_px = int(w_pt * scale)
            h_px = int(h_pt * scale)

            overlay = Image.new("RGBA", (w_px, h_px), (255, 255, 255, 0))
            px = (w_px - stamp_w) // 2
            py = (h_px - stamp_h) // 2
            overlay.paste(rotated, (px, py), rotated)

            buf = io.BytesIO()
            overlay.save(buf, "PNG")
            page.insert_image(page.rect, stream=buf.getvalue(), overlay=True)

            if progress_callback:
                progress_callback(page_num + 1, len(self._doc))

            buf = io.BytesIO()
            overlay.save(buf, "PNG")
            page.insert_image(page.rect, stream=buf.getvalue(), overlay=True)

            if progress_callback:
                progress_callback(page_num + 1, len(self._doc))

    # ------------------------------------------------------------------
    # Text annotation  (burned in)
    # ------------------------------------------------------------------

    def add_text(
        self,
        page_index: int,
        text: str,
        x: float,
        y: float,
        font_size: int = 14,
        color: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        font: str = "helv",
    ) -> None:
        """Insert permanent text at (x, y) on a page (PDF coordinates)."""
        if not self._doc:
            raise RuntimeError("No document open.")
        page = self._doc[page_index]
        page.insert_text(
            fitz.Point(x, y),
            text,
            fontsize=font_size,
            color=color,
            fontname=font,
            overlay=True,
        )

    # ------------------------------------------------------------------
    # Signature  (image burned in)
    # ------------------------------------------------------------------

    def add_signature_image(
        self,
        page_index: int,
        img: Image.Image,
        rect: Tuple[float, float, float, float],
    ) -> None:
        """
        Stamp a PIL Image (e.g. a drawn signature) onto the page at `rect`
        (x0, y0, x1, y1) in PDF point coordinates.
        """
        if not self._doc:
            raise RuntimeError("No document open.")
        page = self._doc[page_index]
        img_bytes = _pil_to_bytes(img, "PNG")
        pdf_rect = fitz.Rect(*rect)
        page.insert_image(pdf_rect, stream=img_bytes, overlay=True)

    def add_signature_text(
        self,
        page_index: int,
        text: str,
        x: float,
        y: float,
        font_size: int = 24,
        color: Tuple[float, float, float] = (0.0, 0.0, 0.5),
    ) -> None:
        """Insert a typed signature (italic style via ZapDingbats fallback)."""
        if not self._doc:
            raise RuntimeError("No document open.")
        page = self._doc[page_index]
        page.insert_text(
            fitz.Point(x, y),
            text,
            fontsize=font_size,
            color=color,
            fontname="TiRo",  # Times-Roman italic-like
            overlay=True,
        )

    # ------------------------------------------------------------------
    # Save  (flatten + optional password protection)
    # ------------------------------------------------------------------

    def save(
        self,
        output_path: str | Path,
        owner_password: str = "",
        user_password: str = "",
    ) -> None:
        """
        Save the modified PDF to `output_path`.

        - garbage=4  — remove all orphaned/unreferenced objects
        - deflate=True — compress content streams
        - If owner_password is given, restrict editing/copying/printing so
          the watermark cannot be removed via normal PDF tools.
        """
        if not self._doc:
            raise RuntimeError("No document open.")

        out = Path(output_path)

        save_kwargs: dict = {
            "garbage": 4,
            "deflate": True,
            "clean": True,
        }

        if owner_password:
            perm = (
                fitz.PDF_PERM_PRINT
                | fitz.PDF_PERM_COPY  # allow copy so text is still readable
            )
            # Explicitly forbid editing and annotation removal
            encrypt = fitz.PDF_ENCRYPT_AES_256
            self._doc.save(
                str(out),
                encryption=encrypt,
                owner_pw=owner_password,
                user_pw=user_password,
                permissions=perm,
                **save_kwargs,
            )
        else:
            self._doc.save(str(out), **save_kwargs)

    def split_pages(
        self,
        output_path: str | Path,
        from_page: int,
        to_page: int,
    ) -> int:
        """
        Save pages `from_page` to `to_page` (both 1-based, inclusive) as a
        new PDF.  Returns the number of pages saved.
        """
        if not self._doc:
            raise RuntimeError("No document open.")
        total = len(self._doc)
        if from_page < 1 or to_page > total or from_page > to_page:
            raise ValueError(
                f"Invalid range {from_page}–{to_page} for a {total}-page document."
            )
        new_doc = fitz.open()
        # select() takes 0-based indices
        new_doc.insert_pdf(self._doc,
                           from_page=from_page - 1,
                           to_page=to_page - 1)
        out = Path(output_path)
        new_doc.save(str(out), garbage=4, deflate=True)
        new_doc.close()
        return to_page - from_page + 1

    def save_flattened(
        self,
        output_path: str | Path,
        dpi: int = 200,
        owner_password: str = "",
        user_password: str = "",
        progress_callback=None,
    ) -> None:
        """
        Maximum-protection save: render every page to a raster bitmap at
        `dpi` resolution and rebuild the PDF from those images.

        After this operation the PDF contains only image XObjects — there
        are zero text or vector drawing operators anywhere in the file.
        No PDF editing tool can isolate or remove the watermark because it
        is inseparable from the rest of the page pixels.

        Trade-off: the output is larger and text is no longer selectable.
        """
        if not self._doc:
            raise RuntimeError("No document open.")

        out = Path(output_path)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)

        new_doc = fitz.open()
        total = len(self._doc)
        for i, page in enumerate(self._doc):
            pix = page.get_pixmap(matrix=mat, alpha=False)
            # New page keeps the original point dimensions
            new_page = new_doc.new_page(width=page.rect.width, height=page.rect.height)
            # Insert the fully-rendered bitmap — all layers merged into pixels
            new_page.insert_image(new_page.rect, stream=pix.tobytes("png"))
            if progress_callback:
                progress_callback(i + 1, total)

        save_kwargs: dict = {"garbage": 4, "deflate": True, "clean": True}

        if owner_password:
            perm = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY
            new_doc.save(
                str(out),
                encryption=fitz.PDF_ENCRYPT_AES_256,
                owner_pw=owner_password,
                user_pw=user_password,
                permissions=perm,
                **save_kwargs,
            )
        else:
            new_doc.save(str(out), **save_kwargs)

        new_doc.close()
