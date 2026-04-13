"""
PDF Assistant — Tkinter GUI

Tabs:
  1. Watermark  — add diagonal tiled text watermark to all pages
  2. Text Editor — click on the page preview to place text
  3. Signature   — draw a freehand signature OR type one
  4. Save        — save with optional password protection

Navigation: previous / next page buttons + page indicator.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser, simpledialog
from typing import Optional
from pathlib import Path
import io
import threading

from PIL import Image, ImageDraw, ImageTk
from pdf_engine import PDFDocument

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CANVAS_BG = "#f0f0f0"
PREVIEW_ZOOM = 1.5
SIG_CANVAS_W = 500
SIG_CANVAS_H = 200
SIG_PEN_WIDTH = 3

# Signature font options: display name → (PyMuPDF built-in name, fontfile path or None)
# fontfile paths are macOS-specific; missing files fall back to the built-in name.
_SIG_FONTS: dict = {
    "Classic Italic (Times)":       ("tiit", None),
    "Elegant (Snell Roundhand)":    ("tiit", "/System/Library/Fonts/Supplemental/SnellRoundhand.ttc"),
    "Handwritten (Bradley Hand)":   ("tiit", "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf"),
    "Oblique (Helvetica)":          ("heit", None),
    "Italic Courier":               ("cobi", None),
}


# ---------------------------------------------------------------------------
# Helper: PIL Image → PhotoImage (Tkinter-compatible)
# ---------------------------------------------------------------------------

def pil_to_photoimage(img: Image.Image) -> ImageTk.PhotoImage:
    return ImageTk.PhotoImage(img)


# ---------------------------------------------------------------------------
# Signature drawing widget
# ---------------------------------------------------------------------------

class SignatureCanvas(tk.Canvas):
    """A canvas the user can draw on to create a freehand signature."""

    def __init__(self, master, width=SIG_CANVAS_W, height=SIG_CANVAS_H, **kw):
        super().__init__(master, width=width, height=height,
                         bg="white", cursor="crosshair", **kw)
        self._width = width
        self._height = height
        self._pen_color: str = "#000080"   # navy default
        self._pen_width: int = SIG_PEN_WIDTH
        # Each stroke: (hex_color, width, [(x0,y0), (x1,y1), ...])
        self._strokes: list = []
        self._current_pts: list = []
        self._stroke_color: str = self._pen_color
        self._stroke_width: int = self._pen_width

        self._img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        self._draw = ImageDraw.Draw(self._img)
        self._last: Optional[tuple[int, int]] = None

        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    # ---- pen properties ----

    def set_pen_color(self, hex_color: str) -> None:
        self._pen_color = hex_color

    def set_pen_width(self, width: int) -> None:
        self._pen_width = max(1, width)

    # ---- drawing events ----

    def _on_press(self, event):
        self._last = (event.x, event.y)
        self._current_pts = [(event.x, event.y)]
        self._stroke_color = self._pen_color
        self._stroke_width = self._pen_width

    def _on_drag(self, event):
        if self._last:
            x0, y0 = self._last
            x1, y1 = event.x, event.y
            self.create_line(x0, y0, x1, y1,
                             fill=self._stroke_color,
                             width=self._stroke_width,
                             capstyle=tk.ROUND, smooth=True)
            r, g, b = self._hex_to_rgb(self._stroke_color)
            self._draw.line([x0, y0, x1, y1], fill=(r, g, b, 255),
                            width=self._stroke_width)
            self._last = (x1, y1)
            self._current_pts.append((x1, y1))

    def _on_release(self, _event):
        if len(self._current_pts) > 1:
            self._strokes.append(
                (self._stroke_color, self._stroke_width, list(self._current_pts))
            )
        self._last = None
        self._current_pts = []

    # ---- undo / clear ----

    def undo_last_stroke(self) -> None:
        if self._strokes:
            self._strokes.pop()
            self._redraw_all()

    def _redraw_all(self) -> None:
        self.delete("all")
        self._img = Image.new("RGBA", (self._width, self._height), (255, 255, 255, 0))
        self._draw = ImageDraw.Draw(self._img)
        for hex_color, width, pts in self._strokes:
            r, g, b = self._hex_to_rgb(hex_color)
            fill = (r, g, b, 255)
            for i in range(len(pts) - 1):
                x0, y0 = pts[i]
                x1, y1 = pts[i + 1]
                self.create_line(x0, y0, x1, y1,
                                 fill=hex_color, width=width,
                                 capstyle=tk.ROUND, smooth=True)
                self._draw.line([x0, y0, x1, y1], fill=fill, width=width)

    def clear(self):
        self._strokes.clear()
        self._current_pts = []
        self._redraw_all()

    # ---- output ----

    def get_image(self) -> Image.Image:
        """Return the drawn signature as a PIL RGBA image."""
        return self._img.copy()

    def is_empty(self) -> bool:
        return len(self._strokes) == 0

    # ---- helpers ----

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> tuple:
        h = hex_color.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------

class PDFAssistantApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF Assistant")
        self.resizable(True, True)
        self.geometry("1050x800")

        self._doc = PDFDocument()
        self._current_page: int = 0
        self._page_count: int = 0
        self._preview_img: Optional[ImageTk.PhotoImage] = None

        # State for text placement mode
        self._text_placement_active: bool = False
        self._text_color: tuple = (0.0, 0.0, 0.0)

        # State for signature placement
        self._sig_placement_active: bool = False
        self._pending_sig_img: Optional[Image.Image] = None

        # Movable overlay system (deferred signature placement)
        self._overlay_items: list = []
        self._next_overlay_id: int = 0
        self._overlay_photo_refs: dict = {}   # prevent PhotoImage GC
        self._dragging_id: Optional[int] = None
        self._drag_last_canvas: Optional[tuple] = None

        # Typewriter mode
        self._typewriter_active: bool = False

        # Typed signature UI state (tk vars created before _build_ui)
        self._typed_sig_font_var = tk.StringVar(value=list(_SIG_FONTS.keys())[0])
        self._typed_sig_size = tk.IntVar(value=24)
        self._typed_sig_color: tuple = (0.0, 0.0, 0.5)

        self._build_menu()
        self._build_ui()

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self):
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open PDF…", accelerator="Cmd+O", command=self._open_file)
        file_menu.add_separator()
        file_menu.add_command(label="Save As…", accelerator="Cmd+Shift+S", command=self._save_file)
        file_menu.add_separator()
        file_menu.add_command(label="Close", command=self._close_file)
        menubar.add_cascade(label="File", menu=file_menu)
        self.config(menu=menubar)
        self.bind_all("<Command-o>", lambda _: self._open_file())
        self.bind_all("<Command-S>", lambda _: self._save_file())

    # ------------------------------------------------------------------
    # UI layout
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ---- Top toolbar ----
        toolbar = ttk.Frame(self, padding=4)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(toolbar, text="Open PDF", command=self._open_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Save As", command=self._save_file).pack(side=tk.LEFT, padx=2)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)

        ttk.Button(toolbar, text="◀ Prev", command=self._prev_page).pack(side=tk.LEFT, padx=2)
        self._page_label = ttk.Label(toolbar, text="No file open")
        self._page_label.pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Next ▶", command=self._next_page).pack(side=tk.LEFT, padx=2)

        # ---- Main paned window (left panel + preview) ----
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # ---- Left panel: tool tabs ----
        left_frame = ttk.Frame(paned, width=300)
        paned.add(left_frame, weight=0)

        self._notebook = ttk.Notebook(left_frame)
        self._notebook.pack(fill=tk.BOTH, expand=True)

        self._build_watermark_tab()
        self._build_text_tab()
        self._build_signature_tab()
        self._build_save_tab()
        self._build_split_tab()

        # ---- Right panel: page preview ----
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)

        self._canvas_frame = ttk.Frame(right_frame)
        self._canvas_frame.pack(fill=tk.BOTH, expand=True)

        h_scroll = ttk.Scrollbar(self._canvas_frame, orient=tk.HORIZONTAL)
        v_scroll = ttk.Scrollbar(self._canvas_frame, orient=tk.VERTICAL)
        self._page_canvas = tk.Canvas(
            self._canvas_frame,
            bg=CANVAS_BG,
            xscrollcommand=h_scroll.set,
            yscrollcommand=v_scroll.set,
        )
        h_scroll.config(command=self._page_canvas.xview)
        v_scroll.config(command=self._page_canvas.yview)
        h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._page_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._page_canvas.bind("<Button-1>", self._on_canvas_click)
        self._page_canvas.bind("<B1-Motion>", self._do_drag)
        self._page_canvas.bind("<ButtonRelease-1>", self._end_drag)

    # ------------------------------------------------------------------
    # Watermark tab
    # ------------------------------------------------------------------

    def _build_watermark_tab(self):
        frame = ttk.Frame(self._notebook, padding=10)
        self._notebook.add(frame, text="Watermark")

        ttk.Label(frame, text="Watermark Text:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self._wm_text_widget = tk.Text(frame, width=22, height=3, wrap=tk.WORD,
                                       font=("TkDefaultFont", 9))
        self._wm_text_widget.insert("1.0", "CONFIDENTIAL")
        self._wm_text_widget.grid(row=0, column=1, pady=4)
        ttk.Label(frame, text="(Press Enter for\na new line)",
                  foreground="gray", font=("TkDefaultFont", 8)).grid(
                  row=0, column=2, padx=4, sticky=tk.W)

        ttk.Label(frame, text="Font Size:").grid(row=1, column=0, sticky=tk.W, pady=4)
        self._wm_size = tk.IntVar(value=80)
        ttk.Spinbox(frame, from_=10, to=200, textvariable=self._wm_size, width=6).grid(row=1, column=1, sticky=tk.W)

        ttk.Label(frame, text="Opacity (0–1):").grid(row=2, column=0, sticky=tk.W, pady=4)
        self._wm_opacity = tk.DoubleVar(value=0.75)
        ttk.Scale(frame, from_=0.05, to=1.0, variable=self._wm_opacity,
                  orient=tk.HORIZONTAL, length=140).grid(row=2, column=1)

        ttk.Label(frame, text="Angle (°):").grid(row=3, column=0, sticky=tk.W, pady=4)
        self._wm_angle = tk.DoubleVar(value=45.0)
        ttk.Scale(frame, from_=0, to=90, variable=self._wm_angle,
                  orient=tk.HORIZONTAL, length=140).grid(row=3, column=1)

        # Color picker
        self._wm_color = (0.8, 0.0, 0.0)
        self._wm_color_btn = tk.Button(frame, text="  Watermark Color  ",
                                       bg="#cc0000", fg="white", command=self._pick_wm_color)
        self._wm_color_btn.grid(row=4, column=0, columnspan=2, pady=8)

        self._wm_apply_btn = ttk.Button(frame, text="Apply Watermark to All Pages",
                   command=self._apply_watermark)
        self._wm_apply_btn.grid(row=5, column=0, columnspan=2, pady=6)

        self._wm_progress = ttk.Progressbar(frame, orient=tk.HORIZONTAL,
                                             length=200, mode="determinate")
        self._wm_progress.grid(row=6, column=0, columnspan=2, pady=(2, 0))
        self._wm_progress.grid_remove()  # hidden until processing starts

        self._wm_status = ttk.Label(frame, text="", foreground="green")
        self._wm_status.grid(row=7, column=0, columnspan=2)

    def _pick_wm_color(self):
        init = "#{:02x}{:02x}{:02x}".format(
            int(self._wm_color[0] * 255),
            int(self._wm_color[1] * 255),
            int(self._wm_color[2] * 255),
        )
        result = colorchooser.askcolor(color=init, title="Pick Watermark Color")
        if result and result[0]:
            r, g, b = result[0]
            self._wm_color = (r / 255, g / 255, b / 255)
            hex_color = result[1]
            luma = 0.299 * r + 0.587 * g + 0.114 * b
            fg = "white" if luma < 160 else "black"
            self._wm_color_btn.config(bg=hex_color, fg=fg)

    def _apply_watermark(self):
        if not self._require_open():
            return
        text = self._wm_text_widget.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Input required", "Please enter watermark text.")
            return

        self._wm_status.config(text="Starting…", foreground="orange")
        self._wm_apply_btn.config(state="disabled")
        self._wm_progress["value"] = 0
        self._wm_progress["maximum"] = self._page_count
        self._wm_progress.grid()  # show the bar

        kwargs = dict(
            text=text,
            opacity=self._wm_opacity.get(),
            font_size=self._wm_size.get(),
            color=self._wm_color,
            angle=self._wm_angle.get(),
        )

        def on_progress(current: int, total: int):
            self.after(0, self._watermark_progress, current, total)

        def worker():
            try:
                self._doc.apply_watermark(**kwargs, progress_callback=on_progress)
                self.after(0, self._watermark_done, None)
            except Exception as exc:
                self.after(0, self._watermark_done, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _watermark_progress(self, current: int, total: int):
        self._wm_progress["value"] = current
        self._wm_status.config(
            text=f"Page {current} of {total}…",
            foreground="orange",
        )

    def _watermark_done(self, error: str | None):
        self._wm_apply_btn.config(state="normal")
        self._wm_progress.grid_remove()  # hide the bar
        if error:
            self._wm_status.config(text=f"Error: {error}", foreground="red")
        else:
            self._wm_status.config(text="Watermark applied to all pages!", foreground="green")
            self._refresh_preview()

    # ------------------------------------------------------------------
    # Text Editor tab
    # ------------------------------------------------------------------

    def _build_text_tab(self):
        frame = ttk.Frame(self._notebook, padding=10)
        self._notebook.add(frame, text="Text Editor")

        ttk.Label(frame, text="Text to insert:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self._txt_input = tk.Text(frame, width=24, height=4, wrap=tk.WORD)
        self._txt_input.grid(row=1, column=0, columnspan=2, pady=4)

        ttk.Label(frame, text="Font Size:").grid(row=2, column=0, sticky=tk.W, pady=4)
        self._txt_size = tk.IntVar(value=14)
        ttk.Spinbox(frame, from_=6, to=72, textvariable=self._txt_size, width=6).grid(row=2, column=1, sticky=tk.W)

        # Color
        self._txt_color = (0.0, 0.0, 0.0)
        self._txt_color_btn = tk.Button(frame, text="  Text Color  ",
                                        bg="#000000", fg="white",
                                        command=self._pick_txt_color)
        self._txt_color_btn.grid(row=3, column=0, columnspan=2, pady=6)

        ttk.Button(frame, text="Place Text on Page (click canvas)",
                   command=self._activate_text_placement).grid(row=4, column=0, columnspan=2, pady=6)

        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=5, column=0, columnspan=2, sticky=tk.EW, pady=8)

        ttk.Button(frame, text="✏  Typewriter Mode",
                   command=self._activate_typewriter_mode).grid(row=6, column=0, columnspan=2, pady=4)
        ttk.Button(frame, text="Stop Typewriter",
                   command=self._stop_typewriter_mode).grid(row=7, column=0, columnspan=2, pady=2)

        self._txt_status = ttk.Label(frame, text="", foreground="green")
        self._txt_status.grid(row=8, column=0, columnspan=2, pady=4)

        ttk.Label(frame, text="Click the page preview\nafter pressing a button\nto place your text.",
                  justify=tk.CENTER, foreground="gray").grid(row=9, column=0, columnspan=2)

    def _pick_txt_color(self):
        init = "#{:02x}{:02x}{:02x}".format(
            int(self._txt_color[0] * 255),
            int(self._txt_color[1] * 255),
            int(self._txt_color[2] * 255),
        )
        result = colorchooser.askcolor(color=init, title="Pick Text Color")
        if result and result[0]:
            r, g, b = result[0]
            self._txt_color = (r / 255, g / 255, b / 255)
            hex_color = result[1]
            luma = 0.299 * r + 0.587 * g + 0.114 * b
            fg = "white" if luma < 128 else "black"
            self._txt_color_btn.config(bg=hex_color, fg=fg)

    def _activate_text_placement(self):
        if not self._require_open():
            return
        text = self._txt_input.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Input required", "Please type some text first.")
            return
        self._text_placement_active = True
        self._sig_placement_active = False
        self._txt_status.config(text="Click on the page to place text.")
        self._page_canvas.config(cursor="crosshair")

    # ------------------------------------------------------------------
    # Signature tab
    # ------------------------------------------------------------------

    def _build_signature_tab(self):
        frame = ttk.Frame(self._notebook, padding=10)
        self._notebook.add(frame, text="Signature")

        # ---- Pen controls ----
        pen_row = ttk.Frame(frame)
        pen_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(pen_row, text="Pen:").pack(side=tk.LEFT)
        self._sig_pen_color_hex = "#000080"
        self._sig_pen_color_btn = tk.Button(
            pen_row, text="  Colour  ", bg="#000080", fg="white",
            command=self._pick_sig_pen_color)
        self._sig_pen_color_btn.pack(side=tk.LEFT, padx=4)
        ttk.Label(pen_row, text="Width:").pack(side=tk.LEFT, padx=(8, 0))
        self._sig_pen_width = tk.IntVar(value=SIG_PEN_WIDTH)
        self._sig_pen_width.trace_add("write", lambda *_: self._update_pen_width())
        ttk.Spinbox(pen_row, from_=1, to=10, textvariable=self._sig_pen_width,
                    width=4).pack(side=tk.LEFT, padx=4)

        # ---- Drawing canvas ----
        ttk.Label(frame, text="Draw your signature:").pack(anchor=tk.W, pady=(4, 2))
        self._sig_canvas = SignatureCanvas(frame, width=270, height=120)
        self._sig_canvas.pack(pady=4)

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, pady=2)
        ttk.Button(btn_row, text="Clear",
                   command=self._sig_canvas.clear).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="Undo Stroke",
                   command=self._sig_canvas.undo_last_stroke).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="Place Drawn Sig",
                   command=self._activate_drawn_sig).pack(side=tk.LEFT, padx=2)

        ttk.Button(frame, text="Upload Signature Image…",
                   command=self._upload_sig_image).pack(pady=(4, 2))

        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        # ---- Typed signature ----
        ttk.Label(frame, text="— OR type a signature —").pack(pady=2)
        self._typed_sig = tk.StringVar()
        ttk.Entry(frame, textvariable=self._typed_sig, width=28,
                  font=("Times New Roman", 16, "italic")).pack(pady=4)

        font_row = ttk.Frame(frame)
        font_row.pack(fill=tk.X, pady=2)
        ttk.Label(font_row, text="Font:").pack(side=tk.LEFT)
        ttk.Combobox(font_row, textvariable=self._typed_sig_font_var,
                     values=list(_SIG_FONTS.keys()),
                     state="readonly", width=22).pack(side=tk.LEFT, padx=4)

        style_row = ttk.Frame(frame)
        style_row.pack(fill=tk.X, pady=2)
        ttk.Label(style_row, text="Size:").pack(side=tk.LEFT)
        ttk.Spinbox(style_row, from_=8, to=72, textvariable=self._typed_sig_size,
                    width=5).pack(side=tk.LEFT, padx=4)
        self._typed_sig_color_btn = tk.Button(
            style_row, text="  Colour  ", bg="#00007f", fg="white",
            command=self._pick_typed_sig_color)
        self._typed_sig_color_btn.pack(side=tk.LEFT, padx=6)

        ttk.Button(frame, text="Place Typed Signature",
                   command=self._activate_typed_sig).pack(pady=6)

        ttk.Label(frame,
                  text="Drag placed signatures to reposition.\nRight-click a signature to remove it.",
                  foreground="gray", justify=tk.CENTER).pack(pady=2)

        self._sig_status = ttk.Label(frame, text="", foreground="green")
        self._sig_status.pack()

    def _activate_drawn_sig(self):
        if not self._require_open():
            return
        if self._sig_canvas.is_empty():
            messagebox.showwarning("Empty signature", "Please draw your signature first.")
            return
        self._pending_sig_img = self._sig_canvas.get_image()
        self._sig_placement_active = True
        self._text_placement_active = False
        self._sig_status.config(text="Click on the page to stamp signature.")
        self._page_canvas.config(cursor="crosshair")

    def _activate_typed_sig(self):
        if not self._require_open():
            return
        text = self._typed_sig.get().strip()
        if not text:
            messagebox.showwarning("Empty signature", "Please type your signature.")
            return
        self._pending_sig_img = None  # use text path
        self._sig_placement_active = True
        self._text_placement_active = False
        self._sig_status.config(text="Click on the page to stamp typed signature.")
        self._page_canvas.config(cursor="crosshair")

    # ------------------------------------------------------------------
    # Save tab
    # ------------------------------------------------------------------

    def _build_save_tab(self):
        frame = ttk.Frame(self._notebook, padding=10)
        self._notebook.add(frame, text="Save")

        ttk.Label(frame, text="Protect PDF with a password\nto prevent watermark removal:",
                  justify=tk.LEFT).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=6)

        ttk.Label(frame, text="Owner Password\n(prevents editing):").grid(
            row=1, column=0, sticky=tk.W, pady=4)
        self._owner_pw = tk.StringVar()
        ttk.Entry(frame, textvariable=self._owner_pw, show="*", width=20).grid(row=1, column=1, pady=4)

        ttk.Label(frame, text="User Password\n(to open file, optional):").grid(
            row=2, column=0, sticky=tk.W, pady=4)
        self._user_pw = tk.StringVar()
        ttk.Entry(frame, textvariable=self._user_pw, show="*", width=20).grid(row=2, column=1, pady=4)

        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=3, column=0, columnspan=2, sticky=tk.EW, pady=8)

        self._flatten = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame,
            text="Flatten pages to images\n(maximum protection)",
            variable=self._flatten,
        ).grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=4)

        ttk.Label(
            frame,
            text="Flattening converts every page to a\n"
                 "bitmap — no text operators remain\n"
                 "in the file, so the watermark is\n"
                 "100% inseparable from the content.\n"
                 "Note: text will no longer be selectable.",
            foreground="gray",
            justify=tk.LEFT,
        ).grid(row=5, column=0, columnspan=2, pady=2)

        ttk.Label(
            frame,
            text="DPI (100=small, 200=balanced, 300=print):",
            justify=tk.LEFT,
        ).grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=(8, 2))
        self._flatten_dpi = tk.IntVar(value=200)
        dpi_row = ttk.Frame(frame)
        dpi_row.grid(row=7, column=0, columnspan=2, sticky=tk.W)
        for dpi_val in (100, 150, 200, 300):
            ttk.Radiobutton(
                dpi_row, text=str(dpi_val), variable=self._flatten_dpi, value=dpi_val
            ).pack(side=tk.LEFT, padx=4)

        self._save_btn = ttk.Button(frame, text="Save PDF As…",
                   command=self._save_file)
        self._save_btn.grid(row=8, column=0, columnspan=2, pady=12)

        self._save_progress = ttk.Progressbar(frame, orient=tk.HORIZONTAL,
                                               length=200, mode="determinate")
        self._save_progress.grid(row=9, column=0, columnspan=2, pady=(0, 4))
        self._save_progress.grid_remove()

        self._save_status = ttk.Label(frame, text="", foreground="gray")
        self._save_status.grid(row=10, column=0, columnspan=2)

    # ------------------------------------------------------------------
    # Split tab
    # ------------------------------------------------------------------

    def _build_split_tab(self):
        frame = ttk.Frame(self._notebook, padding=10)
        self._notebook.add(frame, text="Split")

        ttk.Label(frame, text="Extract a range of pages\ninto a new PDF file.",
                  justify=tk.LEFT).grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 10))

        # Show total pages dynamically
        self._split_info = ttk.Label(frame, text="Open a PDF to see page count.",
                                     foreground="gray")
        self._split_info.grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(0, 8))

        ttk.Label(frame, text="From page:").grid(row=2, column=0, sticky=tk.W, pady=4)
        self._split_from = tk.IntVar(value=1)
        ttk.Spinbox(frame, from_=1, to=9999, textvariable=self._split_from,
                    width=6).grid(row=2, column=1, sticky=tk.W, padx=4)

        ttk.Label(frame, text="To page:").grid(row=3, column=0, sticky=tk.W, pady=4)
        self._split_to = tk.IntVar(value=1)
        ttk.Spinbox(frame, from_=1, to=9999, textvariable=self._split_to,
                    width=6).grid(row=3, column=1, sticky=tk.W, padx=4)

        ttk.Button(frame, text="Set to Last Page",
                   command=self._split_set_last).grid(row=3, column=2, padx=4)

        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(
            row=4, column=0, columnspan=3, sticky=tk.EW, pady=10)

        ttk.Button(frame, text="Extract Pages to New PDF…",
                   command=self._do_split).grid(row=5, column=0, columnspan=3, pady=4)

        self._split_status = ttk.Label(frame, text="", foreground="green")
        self._split_status.grid(row=6, column=0, columnspan=3, pady=4)

    def _split_set_last(self):
        if self._page_count:
            self._split_to.set(self._page_count)

    def _do_split(self):
        if not self._require_open():
            return
        from_p = self._split_from.get()
        to_p = self._split_to.get()
        if from_p < 1 or to_p < from_p or to_p > self._page_count:
            messagebox.showwarning(
                "Invalid range",
                f"Please enter a valid range between 1 and {self._page_count}."
            )
            return
        path = filedialog.asksaveasfilename(
            title="Save Extracted Pages As",
            defaultextension=".pdf",
            filetypes=[("PDF Files", "*.pdf")],
        )
        if not path:
            return
        try:
            count = self._doc.split_pages(path, from_p, to_p)
            self._split_status.config(
                text=f"Saved {count} page(s) to:\n{Path(path).name}",
                foreground="green",
            )
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    # ------------------------------------------------------------------
    # Canvas interaction
    # ------------------------------------------------------------------

    def _on_canvas_click(self, event):
        # Suppress if a drag is in progress (started on an overlay item)
        if self._dragging_id is not None:
            return
        if not self._doc or self._page_count == 0:
            return

        # Convert canvas coords → PDF point coords
        canvas_x = self._page_canvas.canvasx(event.x)
        canvas_y = self._page_canvas.canvasy(event.y)

        page = self._doc._doc[self._current_page]
        pdf_w = page.rect.width
        pdf_h = page.rect.height

        offset_x, offset_y = 10, 10
        img_x = canvas_x - offset_x
        img_y = canvas_y - offset_y

        img_w = pdf_w * PREVIEW_ZOOM
        img_h = pdf_h * PREVIEW_ZOOM

        if img_x < 0 or img_y < 0 or img_x > img_w or img_y > img_h:
            return  # clicked outside page

        pdf_x = (img_x / img_w) * pdf_w
        pdf_y = (img_y / img_h) * pdf_h

        if self._text_placement_active:
            text = self._txt_input.get("1.0", tk.END).strip()
            self._doc.add_text(
                self._current_page, text, pdf_x, pdf_y,
                font_size=self._txt_size.get(), color=self._txt_color,
            )
            self._text_placement_active = False
            self._page_canvas.config(cursor="")
            self._txt_status.config(text="Text placed!")
            self._refresh_preview()

        elif self._sig_placement_active:
            ov_id = self._next_overlay_id
            self._next_overlay_id += 1

            if self._pending_sig_img is not None:
                sig_w_pt = 150.0
                sig_h_pt = 60.0
                overlay = {
                    "id": ov_id,
                    "type": "sig_image",
                    "page": self._current_page,
                    "pdf_x": pdf_x,
                    "pdf_y": pdf_y - sig_h_pt,
                    "pdf_w": sig_w_pt,
                    "pdf_h": sig_h_pt,
                    "data": self._pending_sig_img.copy(),
                    "canvas_item": None,
                }
            else:
                text = self._typed_sig.get().strip()
                font_key = self._typed_sig_font_var.get()
                fontname, fontfile = _SIG_FONTS.get(font_key, ("tiit", None))
                overlay = {
                    "id": ov_id,
                    "type": "sig_text",
                    "page": self._current_page,
                    "pdf_x": pdf_x,
                    "pdf_y": pdf_y,
                    "pdf_w": 0.0,
                    "pdf_h": 0.0,
                    "data": text,
                    "font_size": self._typed_sig_size.get(),
                    "color": self._typed_sig_color,
                    "fontname": fontname,
                    "fontfile": fontfile,
                    "canvas_item": None,
                }

            self._overlay_items.append(overlay)
            self._sig_placement_active = False
            self._pending_sig_img = None
            self._page_canvas.config(cursor="")
            self._sig_status.config(text="Placed! Drag to reposition, right-click to remove.")
            self._add_overlay_to_canvas(overlay)

        elif self._typewriter_active:
            self._start_typewriter_entry(canvas_x, canvas_y, pdf_x, pdf_y)

    # ------------------------------------------------------------------
    # Signature pen controls
    # ------------------------------------------------------------------

    def _pick_sig_pen_color(self):
        result = colorchooser.askcolor(color=self._sig_pen_color_hex,
                                       title="Pick Pen Colour")
        if result and result[1]:
            self._sig_pen_color_hex = result[1]
            r, g, b = result[0]
            luma = 0.299 * r + 0.587 * g + 0.114 * b
            fg = "white" if luma < 160 else "black"
            self._sig_pen_color_btn.config(bg=result[1], fg=fg)
            self._sig_canvas.set_pen_color(result[1])

    def _update_pen_width(self):
        try:
            self._sig_canvas.set_pen_width(self._sig_pen_width.get())
        except tk.TclError:
            pass

    def _pick_typed_sig_color(self):
        init = "#{:02x}{:02x}{:02x}".format(
            int(self._typed_sig_color[0] * 255),
            int(self._typed_sig_color[1] * 255),
            int(self._typed_sig_color[2] * 255),
        )
        result = colorchooser.askcolor(color=init, title="Pick Signature Colour")
        if result and result[0]:
            r, g, b = result[0]
            self._typed_sig_color = (r / 255, g / 255, b / 255)
            hex_color = result[1]
            luma = 0.299 * r + 0.587 * g + 0.114 * b
            fg = "white" if luma < 128 else "black"
            self._typed_sig_color_btn.config(bg=hex_color, fg=fg)

    # ------------------------------------------------------------------
    # Upload signature image
    # ------------------------------------------------------------------

    def _upload_sig_image(self):
        if not self._require_open():
            return
        path = filedialog.askopenfilename(
            title="Open Signature Image",
            filetypes=[
                ("Image Files", "*.png *.jpg *.jpeg *.bmp *.gif"),
                ("All Files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            img = Image.open(path).convert("RGBA")
        except Exception as exc:
            messagebox.showerror("Error", f"Cannot open image:\n{exc}")
            return
        self._pending_sig_img = img
        self._sig_placement_active = True
        self._text_placement_active = False
        self._typewriter_active = False
        self._sig_status.config(text="Click on the page to stamp the image.")
        self._page_canvas.config(cursor="crosshair")

    # ------------------------------------------------------------------
    # Typewriter mode
    # ------------------------------------------------------------------

    def _activate_typewriter_mode(self):
        if not self._require_open():
            return
        self._typewriter_active = True
        self._text_placement_active = False
        self._sig_placement_active = False
        self._page_canvas.config(cursor="xterm")
        self._txt_status.config(text="Typewriter ON \u2014 click page to type. Esc to stop.")

    def _stop_typewriter_mode(self):
        self._typewriter_active = False
        self._page_canvas.config(cursor="")
        self._txt_status.config(text="Typewriter stopped.")

    def _start_typewriter_entry(self, canvas_x: float, canvas_y: float,
                                pdf_x: float, pdf_y: float) -> None:
        entry = tk.Entry(
            self._page_canvas,
            font=("Courier New", 12),
            bd=1, relief=tk.SOLID,
            highlightthickness=1,
            highlightcolor="royalblue",
            width=22,
        )
        win = self._page_canvas.create_window(
            canvas_x, canvas_y, window=entry, anchor=tk.W)
        entry.focus_set()

        committed = [False]

        def commit(e=None):
            if committed[0]:
                return
            committed[0] = True
            text = entry.get().strip()
            entry.unbind("<FocusOut>")
            self._page_canvas.delete(win)
            entry.destroy()
            if text:
                self._doc.add_text(
                    self._current_page, text, pdf_x, pdf_y,
                    font_size=self._txt_size.get(),
                    color=self._txt_color,
                )
                self._refresh_preview()

        def cancel(e=None):
            if committed[0]:
                return
            committed[0] = True
            entry.unbind("<FocusOut>")
            self._page_canvas.delete(win)
            entry.destroy()
            self._stop_typewriter_mode()

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", cancel)

    # ------------------------------------------------------------------
    # Overlay canvas helpers
    # ------------------------------------------------------------------

    def _redraw_overlays(self) -> None:
        """Re-draw all pending overlay items for the current page on the canvas."""
        for ov in self._overlay_items:
            if ov["page"] == self._current_page:
                ov["canvas_item"] = None  # deleted by _refresh_preview's delete("all")
                self._overlay_photo_refs.pop(ov["id"], None)
                self._add_overlay_to_canvas(ov)

    def _add_overlay_to_canvas(self, overlay: dict) -> None:
        ov_id = overlay["id"]
        tag = f"ov_{ov_id}"

        cx = overlay["pdf_x"] * PREVIEW_ZOOM + 10
        cy = overlay["pdf_y"] * PREVIEW_ZOOM + 10

        if overlay["type"] == "sig_image":
            disp_w = max(1, int(overlay["pdf_w"] * PREVIEW_ZOOM))
            disp_h = max(1, int(overlay["pdf_h"] * PREVIEW_ZOOM))
            disp_img = overlay["data"].resize((disp_w, disp_h), Image.LANCZOS)
            photo = ImageTk.PhotoImage(disp_img)
            self._overlay_photo_refs[ov_id] = photo
            item_id = self._page_canvas.create_image(
                cx, cy, image=photo, anchor=tk.NW,
                tags=("overlay", tag),
            )
        else:
            r, g, b = overlay["color"]
            hex_fill = "#{:02x}{:02x}{:02x}".format(
                int(r * 255), int(g * 255), int(b * 255))
            font_px = max(6, int(overlay["font_size"] * PREVIEW_ZOOM))
            item_id = self._page_canvas.create_text(
                cx, cy,
                text=overlay["data"],
                fill=hex_fill,
                font=("Times New Roman", font_px, "italic"),
                anchor=tk.SW,
                tags=("overlay", tag),
            )

        overlay["canvas_item"] = item_id

        # Bind drag to reposition
        self._page_canvas.tag_bind(
            tag, "<ButtonPress-1>",
            lambda e, i=ov_id: self._start_drag(e, i),
        )
        # Right-click / secondary click to show remove menu
        self._page_canvas.tag_bind(
            tag, "<Button-2>",
            lambda e, i=ov_id: self._remove_overlay_menu(e, i),
        )
        self._page_canvas.tag_bind(
            tag, "<Button-3>",
            lambda e, i=ov_id: self._remove_overlay_menu(e, i),
        )

    # ------------------------------------------------------------------
    # Drag-to-reposition overlay
    # ------------------------------------------------------------------

    def _start_drag(self, event, overlay_id: int) -> str:
        self._dragging_id = overlay_id
        self._drag_last_canvas = (
            self._page_canvas.canvasx(event.x),
            self._page_canvas.canvasy(event.y),
        )
        return "break"  # stop propagation → _on_canvas_click won't fire

    def _do_drag(self, event) -> None:
        if self._dragging_id is None:
            return
        cx = self._page_canvas.canvasx(event.x)
        cy = self._page_canvas.canvasy(event.y)
        lx, ly = self._drag_last_canvas
        dx, dy = cx - lx, cy - ly
        for ov in self._overlay_items:
            if ov["id"] == self._dragging_id and ov.get("canvas_item") is not None:
                self._page_canvas.move(ov["canvas_item"], dx, dy)
                break
        self._drag_last_canvas = (cx, cy)

    def _end_drag(self, event) -> None:
        if self._dragging_id is None:
            return
        drag_id = self._dragging_id
        self._dragging_id = None
        self._drag_last_canvas = None
        for ov in self._overlay_items:
            if ov["id"] == drag_id and ov.get("canvas_item") is not None:
                coords = self._page_canvas.coords(ov["canvas_item"])
                if coords:
                    cx, cy = coords[0], coords[1]
                    ov["pdf_x"] = (cx - 10) / PREVIEW_ZOOM
                    ov["pdf_y"] = (cy - 10) / PREVIEW_ZOOM
                break

    # ------------------------------------------------------------------
    # Remove overlay
    # ------------------------------------------------------------------

    def _remove_overlay(self, overlay_id: int) -> None:
        for i, ov in enumerate(self._overlay_items):
            if ov["id"] == overlay_id:
                if ov.get("canvas_item") is not None:
                    self._page_canvas.delete(ov["canvas_item"])
                self._overlay_photo_refs.pop(overlay_id, None)
                self._overlay_items.pop(i)
                break

    def _remove_overlay_menu(self, event, overlay_id: int) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Remove Signature",
                         command=lambda: self._remove_overlay(overlay_id))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def _open_file(self):
        path = filedialog.askopenfilename(
            title="Open PDF",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
        )
        if not path:
            return
        try:
            count = self._doc.open(path)
            self._page_count = count
            self._current_page = 0
            self.title(f"PDF Assistant — {Path(path).name}")
            self._refresh_preview()
        except Exception as exc:
            messagebox.showerror("Error opening file", str(exc))

    def _close_file(self):
        self._doc.close()
        self._overlay_items.clear()
        self._overlay_photo_refs.clear()
        self._page_count = 0
        self._current_page = 0
        self._page_canvas.delete("all")
        self._page_label.config(text="No file open")
        self.title("PDF Assistant")

    def _save_file(self):
        if not self._require_open():
            return
        path = filedialog.asksaveasfilename(
            title="Save PDF As",
            defaultextension=".pdf",
            filetypes=[("PDF Files", "*.pdf")],
        )
        if not path:
            return

        owner_pw = self._owner_pw.get()
        user_pw = self._user_pw.get()
        flatten = self._flatten.get()
        dpi = self._flatten_dpi.get()

        self._save_btn.config(state="disabled", text="Saving\u2026")
        self._save_progress["value"] = 0
        self._save_progress["maximum"] = self._page_count
        self._save_progress.grid()
        self._save_status.config(text="Starting\u2026", foreground="orange")

        def on_progress(current: int, total: int):
            self.after(0, self._save_progress_update, current, total)

        def worker():
            try:
                self._doc.apply_pending_overlays(self._overlay_items)
                if flatten:
                    self._doc.save_flattened(path, dpi=dpi,
                                             owner_password=owner_pw,
                                             user_password=user_pw,
                                             progress_callback=on_progress)
                    mode = f"flattened at {dpi} DPI"
                else:
                    self._doc.save(path, owner_password=owner_pw,
                                   user_password=user_pw)
                    mode = "content-stream mode"
                self.after(0, self._save_done, path, mode, None)
            except Exception as exc:
                self.after(0, self._save_done, path, "", str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _save_progress_update(self, current: int, total: int):
        self._save_progress["value"] = current
        self._save_status.config(
            text=f"Saving page {current} of {total}\u2026",
            foreground="orange",
        )

    def _save_done(self, path: str, mode: str, error: str | None):
        self._save_btn.config(state="normal", text="Save PDF As\u2026")
        self._save_progress.grid_remove()
        if error:
            self._save_status.config(text=f"Error: {error}", foreground="red")
            messagebox.showerror("Error saving file", error)
        else:
            self._overlay_items.clear()
            self._overlay_photo_refs.clear()
            self._save_status.config(
                text=f"Saved ({mode})", foreground="green"
            )
            messagebox.showinfo("Saved", f"PDF saved ({mode}):\n{path}")
            self._refresh_preview()

    # ------------------------------------------------------------------
    # Page navigation
    # ------------------------------------------------------------------

    def _prev_page(self):
        if self._current_page > 0:
            self._current_page -= 1
            self._refresh_preview()

    def _next_page(self):
        if self._current_page < self._page_count - 1:
            self._current_page += 1
            self._refresh_preview()

    # ------------------------------------------------------------------
    # Preview rendering
    # ------------------------------------------------------------------

    def _refresh_preview(self):
        if not self._doc or self._page_count == 0:
            return
        img = self._doc.render_page(self._current_page, zoom=PREVIEW_ZOOM)
        self._preview_img = pil_to_photoimage(img)

        self._page_canvas.delete("all")
        self._page_canvas.create_image(10, 10, anchor=tk.NW, image=self._preview_img)
        self._page_canvas.config(scrollregion=(0, 0, img.width + 20, img.height + 20))
        self._page_label.config(
            text=f"Page {self._current_page + 1} / {self._page_count}"
        )
        # Update split tab
        self._split_info.config(
            text=f"Document has {self._page_count} page(s).",
            foreground="black",
        )
        self._split_to.set(self._page_count)
        self._redraw_overlays()

    # ------------------------------------------------------------------
    # Guard
    # ------------------------------------------------------------------

    def _require_open(self) -> bool:
        if not self._doc or self._page_count == 0:
            messagebox.showinfo("No file", "Please open a PDF file first.")
            return False
        return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = PDFAssistantApp()
    app.mainloop()
