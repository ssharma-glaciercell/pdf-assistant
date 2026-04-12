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
        self._img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        self._draw = ImageDraw.Draw(self._img)
        self._last: Optional[tuple[int, int]] = None
        self._width = width
        self._height = height

        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _on_press(self, event):
        self._last = (event.x, event.y)

    def _on_drag(self, event):
        if self._last:
            x0, y0 = self._last
            x1, y1 = event.x, event.y
            self.create_line(x0, y0, x1, y1, fill="navy",
                             width=SIG_PEN_WIDTH, capstyle=tk.ROUND, smooth=True)
            self._draw.line([x0, y0, x1, y1], fill=(0, 0, 128, 255),
                            width=SIG_PEN_WIDTH)
            self._last = (x1, y1)

    def _on_release(self, _event):
        self._last = None

    def clear(self):
        self.delete("all")
        self._img = Image.new("RGBA", (self._width, self._height), (255, 255, 255, 0))
        self._draw = ImageDraw.Draw(self._img)

    def get_image(self) -> Image.Image:
        """Return the drawn signature as a PIL RGBA image."""
        return self._img.copy()

    def is_empty(self) -> bool:
        extrema = self._img.convert("L").getextrema()
        return extrema == (255, 255)


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
        self._text_color: tuple[float, float, float] = (0.0, 0.0, 0.0)

        # State for signature placement
        self._sig_placement_active: bool = False
        self._pending_sig_img: Optional[Image.Image] = None

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

        self._txt_status = ttk.Label(frame, text="", foreground="green")
        self._txt_status.grid(row=5, column=0, columnspan=2)

        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=6, column=0, columnspan=2, sticky=tk.EW, pady=8)
        ttk.Label(frame, text="Click the page preview\nafter pressing the button\nto place your text.",
                  justify=tk.CENTER, foreground="gray").grid(row=7, column=0, columnspan=2)

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

        ttk.Label(frame, text="Draw your signature below:").pack(anchor=tk.W, pady=4)
        self._sig_canvas = SignatureCanvas(frame, width=270, height=120)
        self._sig_canvas.pack(pady=4)

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, pady=4)
        ttk.Button(btn_row, text="Clear", command=self._sig_canvas.clear).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="Place Drawn Signature",
                   command=self._activate_drawn_sig).pack(side=tk.LEFT, padx=4)

        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        ttk.Label(frame, text="— OR type a signature —").pack(pady=4)
        self._typed_sig = tk.StringVar()
        ttk.Entry(frame, textvariable=self._typed_sig, width=28,
                  font=("Times New Roman", 16, "italic")).pack(pady=4)
        ttk.Button(frame, text="Place Typed Signature",
                   command=self._activate_typed_sig).pack(pady=6)

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
        if not self._doc or self._page_count == 0:
            return

        # Convert canvas coords → PDF point coords
        canvas_x = self._page_canvas.canvasx(event.x)
        canvas_y = self._page_canvas.canvasy(event.y)

        page = self._doc._doc[self._current_page]
        pdf_w = page.rect.width
        pdf_h = page.rect.height

        # The preview image is rendered at PREVIEW_ZOOM; find image offset on canvas
        # Image is placed at (10, 10) on canvas
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
                self._current_page,
                text,
                pdf_x,
                pdf_y,
                font_size=self._txt_size.get(),
                color=self._txt_color,
            )
            self._text_placement_active = False
            self._page_canvas.config(cursor="")
            self._txt_status.config(text="Text placed!")
            self._refresh_preview()

        elif self._sig_placement_active:
            if self._pending_sig_img is not None:
                # Drawn signature: 200px wide box in PDF coords
                sig_w_pt = 150
                sig_h_pt = 60
                rect = (pdf_x, pdf_y - sig_h_pt, pdf_x + sig_w_pt, pdf_y)
                self._doc.add_signature_image(self._current_page, self._pending_sig_img, rect)
            else:
                # Typed signature
                text = self._typed_sig.get().strip()
                self._doc.add_signature_text(
                    self._current_page, text, pdf_x, pdf_y, font_size=24
                )
            self._sig_placement_active = False
            self._pending_sig_img = None
            self._page_canvas.config(cursor="")
            self._sig_status.config(text="Signature placed!")
            self._refresh_preview()

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
            self._save_status.config(
                text=f"Saved ({mode})", foreground="green"
            )
            messagebox.showinfo("Saved", f"PDF saved ({mode}):\n{path}")

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
