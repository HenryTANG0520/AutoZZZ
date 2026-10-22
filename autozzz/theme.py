"""DPI-aware ZZZ compendium/settings skin with locally bundled game typefaces."""
from __future__ import annotations

from functools import lru_cache
import sys
import tkinter as tk
from tkinter import font as tkfont, ttk
from PIL import Image, ImageDraw, ImageFont, ImageTk
from .config import RESOURCE_ROOT

BG = "#0c0c0c"
PANEL = "#343434"
FIELD = "#161616"
LINE = "#565656"
TEXT = "#eeeeeb"
MUTED = "#b0b0ad"
ACCENT = "#f4df00"
ORANGE = "#ff6b22"
SUCCESS = "#83ce19"
WARNING = "#efb84a"
ERROR = "#ff8476"
FONT = "Microsoft YaHei UI"
UI_ASSETS = RESOURCE_ROOT / "assets" / "ui"
_registered = False


def load_fonts(root):
    global FONT, _registered
    if sys.platform == "win32" and not _registered:
        import ctypes
        for name in ("inpin.ttf", "mont-heavy.ttf"):
            path = UI_ASSETS / name
            if path.exists():
                ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0)
        _registered = True
    families = tkfont.families(root)
    FONT = next((name for name in families if "鸿蒙体GBK" in name),
                next((name for name in families if name.lower() == "yinpinhongmengtijianfan"), "Microsoft YaHei UI"))


@lru_cache(maxsize=96)
def bitmap_font(size, latin=False):
    return ImageFont.truetype(str(UI_ASSETS / ("mont-heavy.ttf" if latin else "inpin.ttf")), max(1, int(size)))


@lru_cache(maxsize=12)
def asset_image(path):
    with Image.open(path) as source:
        return source.convert("RGBA")


class Painter:
    """Logical-pixel drawing at twice the window DPI for smooth type and rims."""
    def __init__(self, width, height, scale, background=PANEL):
        self.factor = scale * 2
        self.width, self.height = width / scale, height / scale
        self.image = Image.new("RGBA" if background is None else "RGB", (width * 2, height * 2),
                               (0, 0, 0, 0) if background is None else background)
        self.draw = ImageDraw.Draw(self.image)

    def coords(self, values):
        return tuple(round(v * self.factor) for v in values)

    def box(self, rect, radius=0, fill=FIELD, outline=None, width=1):
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            return  # Geometry can briefly collapse while Tk negotiates a resize.
        self.draw.rounded_rectangle(self.coords(rect), radius=round(radius * self.factor),
                                    fill=fill, outline=outline, width=max(1, round(width * self.factor)))

    def line(self, points, fill=LINE, width=1):
        self.draw.line(self.coords(points), fill=fill, width=max(1, round(width * self.factor)))

    def poly(self, points, fill=TEXT):
        self.draw.polygon(self.coords(points), fill=fill)

    def text(self, xy, text, size=16, fill=TEXT, anchor="lt", latin=False, stroke=0, stroke_fill=BG):
        if "\n" in text:
            for index, line in enumerate(text.splitlines()):
                self.text((xy[0], xy[1]+index*(size+6)), line, size, fill, anchor, latin, stroke, stroke_fill)
            return
        # The website's Mont file is a letter-only subset. Use the full game
        # face for numbers, punctuation and spaces instead of missing glyphs.
        latin = latin and text.isascii() and text.isalpha()
        self.draw.text(self.coords(xy), text, font=bitmap_font(round(size * self.factor), latin),
                       fill=fill, anchor=anchor, stroke_width=round(stroke * self.factor),
                       stroke_fill=stroke_fill, spacing=round(5 * self.factor))

    def wrap(self, text, width, size=16, max_lines=4):
        font = bitmap_font(round(size * self.factor))
        lines, current = [], ""
        for char in text:
            if char == "\n" or (current and font.getlength(current + char) > width * self.factor):
                lines.append(current)
                current = "" if char == "\n" else char
            else:
                current += char
        if current:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1][:-1] + "…"
        return "\n".join(lines)

    def stripes(self, rect, fill="#1b1b1b", step=6):
        x1, y1, x2, y2 = rect
        for x in range(round(x1 - (y2 - y1)), round(x2), step):
            start, end = max(x, x1), min(x + y2 - y1, x2)
            self.line((start, y1 + start - x, end, y1 + end - x), fill)

    def dots(self, rect, fill="#3a3a3a", step=5):
        x1, y1, x2, y2 = rect
        for y in range(round(y1), round(y2), step):
            for x in range(round(x1), round(x2), step):
                self.box((x, y, x + .6, y + .6), fill=fill)

    def asset(self, name, rect, *, template=False):
        path = RESOURCE_ROOT / "assets" / "templates" / name if template else UI_ASSETS / name
        src = asset_image(str(path))
        x1, y1, x2, y2 = self.coords(rect)
        src = src.resize((max(1, x2-x1), max(1, y2-y1)), Image.Resampling.LANCZOS)
        self.image.paste(src, (x1, y1), src)

    def finish(self):
        return self.image.resize((self.image.width // 2, self.image.height // 2), Image.Resampling.LANCZOS)


class Surface(tk.Canvas):
    def __init__(self, parent, scale=1., *, background=PANEL, **kwargs):
        self.scale, self.background, self._paint_job = scale, background, None
        super().__init__(parent, bg=background, highlightthickness=0, borderwidth=0, **kwargs)
        self.bind("<Configure>", self.redraw)
        self.bind("<Destroy>", self._dispose, add="+")

    def _dispose(self, event):
        if event.widget is self and self._paint_job:
            self.after_cancel(self._paint_job)
            self._paint_job = None

    def redraw(self, _event=None):
        if not self._paint_job:
            self._paint_job = self.after_idle(self._render)

    def _render(self):
        self._paint_job = None
        width, height = self.winfo_width(), self.winfo_height()
        if width < 2 or height < 2:
            return
        painter = Painter(width, height, self.scale, self.background)
        self.paint(painter)
        self._photo = ImageTk.PhotoImage(painter.finish(), master=self)
        self.delete("art")
        self.create_image(0, 0, image=self._photo, anchor="nw", tags="art")
        self.tag_lower("art")

    def paint(self, painter):
        pass


def _button_element(root, style, scale, name, *, accent=False, tab=False, option=False):
    photos = []
    for state in ("normal", "active", "pressed", "disabled"):
        p = Painter(round(64 * scale), round(40 * scale), scale, background=PANEL if tab else FIELD if option else BG)
        if option:
            p.box((0, 0, 64, 40), 4, fill=ACCENT if accent else FIELD,
                  outline=ACCENT if state == "active" and not accent else None)
            if not accent:
                p.line((5, 39, 59, 39), "#333333")
        elif tab:
            fill = ACCENT if accent else ("#555555" if state == "active" else "#3d3d3d")
            p.box((1, 2, 63, 46), 10, fill=BG)
            p.box((3, 3, 61, 44), 8, fill=fill, outline="#6b671c" if accent else "#626262")
            p.line((10, 5, 54, 5), "#fff270" if accent else "#777777")
        else:
            fill = (ACCENT if accent else "#171717") if state != "disabled" else "#222222"
            rim = ACCENT if state in ("active", "pressed") or accent else "#555555"
            p.box((0, 0, 64, 40), 20, fill=BG)
            p.box((1, 1, 63, 39), 19, fill=fill, outline="#333333" if state == "disabled" else rim, width=2)
            p.box((5, 5, 59, 35), 15, fill=fill, outline="#c2b628" if accent else "#303030")
        photos.append(ImageTk.PhotoImage(p.finish(), master=root))
    root._game_images.extend(photos)
    normal, active, pressed, disabled = photos
    element = name + ".border"
    style.element_create(element, "image", normal, ("disabled", disabled), ("pressed", pressed),
                         ("active", active), ("focus", active), border=round(18 * scale), padding=0, sticky="nsew")
    style.layout(name, [(element, {"sticky": "nsew", "children": [
        ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})])


def apply_theme(root, scale):
    load_fonts(root)
    d = lambda n: round(n * scale)
    style = ttk.Style(root)
    style.theme_use("clam")
    root._game_images = []
    style.configure(".", font=(FONT, 10), background=PANEL, foreground=TEXT)
    for name, color in (("TFrame", PANEL), ("Card.TFrame", FIELD), ("Panel.TFrame", FIELD),
                        ("Sidebar.TFrame", BG), ("Footer.TFrame", BG), ("Row.TFrame", "#252525")):
        style.configure(name, background=color)
    for name in ("TLabel", "Hint.TLabel", "Subtitle.TLabel", "Kicker.TLabel"):
        style.configure(name, background=PANEL, foreground=MUTED, font=(FONT, 10))
    for name in ("Card.TLabel", "CardHint.TLabel", "CardKicker.TLabel", "CardTitle.TLabel", "Section.TLabel"):
        style.configure(name, background=FIELD, foreground=TEXT, font=(FONT, 10))
    style.configure("CardHint.TLabel", foreground=MUTED, font=("Microsoft YaHei UI", 9))
    style.configure("CardKicker.TLabel", foreground=MUTED, font=(FONT, 11))
    style.configure("PageTitle.TLabel", background=PANEL, foreground=TEXT, font=(FONT, 18))
    style.configure("Footer.TLabel", background=BG, foreground=MUTED, font=("Microsoft YaHei UI", 9))
    style.configure("FooterStatus.TLabel", background=BG, foreground=TEXT, font=(FONT, 10))
    for name, accent, tab, option in (("TButton", False, False, False), ("Accent.TButton", True, False, False),
                              ("Nav.TButton", False, True, False), ("Selected.Nav.TButton", True, True, False),
                              ("Options.TButton", False, False, True), ("Selected.Options.TButton", True, False, True)):
        _button_element(root, style, scale, name, accent=accent, tab=tab, option=option)
        style.configure(name, padding=(d(16 if tab else 15), d(8)), anchor="center", width=0,
                        font=(FONT, 12 if tab else 10), foreground=BG if accent else TEXT,
                        background=PANEL if tab else BG)
        style.map(name, foreground=[("disabled", "#757575"), ("!disabled", BG if accent else TEXT)],
                  background=[("disabled", PANEL if tab else BG), ("active", PANEL if tab else BG)])
    style.configure("Danger.TButton", foreground=ERROR)
    style.map("Danger.TButton", foreground=[("disabled", "#757575"), ("!disabled", ERROR)])
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, fieldbackground=BG, background=BG, foreground=TEXT, bordercolor="#616161",
                        font=("Microsoft YaHei UI", 10),
                        insertcolor=ACCENT, arrowcolor=TEXT, lightcolor=BG, darkcolor=BG, padding=d(6),
                        selectbackground=ACCENT, selectforeground=BG, borderwidth=1)
        style.map(name, fieldbackground=[("readonly", BG), ("disabled", "#282828")],
                  foreground=[("disabled", "#747474"), ("readonly", TEXT)], bordercolor=[("focus", ACCENT)],
                  arrowcolor=[("disabled", "#747474")])
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", BG)
    root.option_add("*TCombobox*Listbox.font", (FONT, 11))
    _field_elements(root, style, scale)
    for name in ("Compact.TEntry", "Compact.TCombobox"):
        style.configure(name, padding=d(3), font=("Microsoft YaHei UI", 9))
    _check_indicators(root, style, scale)
    style.configure("TNotebook", background=PANEL, borderwidth=0, tabmargins=0)
    style.configure("TNotebook.Tab", background=FIELD, foreground=MUTED, padding=(d(22), d(9)))
    style.map("TNotebook.Tab", background=[("selected", ACCENT), ("active", "#505050")],
              foreground=[("selected", BG), ("active", TEXT)])
    style.configure("Vertical.TScrollbar", background=LINE, troughcolor=FIELD, bordercolor=FIELD,
                    arrowcolor=TEXT, lightcolor=FIELD, darkcolor=FIELD)
    style.map("Vertical.TScrollbar", background=[("disabled", "#303030"), ("active", "#777777"), ("!disabled", "#454545")],
              troughcolor=[("disabled", FIELD), ("!disabled", FIELD)],
              arrowcolor=[("disabled", "#666666"), ("!disabled", TEXT)])
    style.configure("TSeparator", background=LINE)
    _window_identity(root)
    return style


def _field_elements(root, style, scale):
    """Native text/selection behavior inside the game's recessed capsule rim."""
    images = []
    for state in ("normal", "focus", "disabled"):
        p = Painter(round(64*scale), round(34*scale), scale, background=FIELD)
        p.box((0, 0, 64, 34), 17, fill=BG, outline=ACCENT if state == "focus" else "#565656", width=2)
        p.box((5, 5, 59, 29), 12, fill=BG, outline="#262626")
        images.append(ImageTk.PhotoImage(p.finish(), master=root))
    root._game_images.extend(images)
    normal, focus, disabled = images
    style.element_create("Game.Field", "image", normal, ("disabled", disabled), ("focus", focus),
                         border=round(16*scale), padding=(round(14*scale), round(3*scale)), sticky="nsew")
    style.layout("TEntry", [("Game.Field", {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})])
    style.layout("TCombobox", [("Game.Field", {"sticky": "nsew", "children": [
        ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
        ("Combobox.padding", {"sticky": "nsew", "children": [("Combobox.textarea", {"sticky": "nsew"})]})]})])
    # Compact battle fields keep the smaller native element to fit eight rows.
    style.layout("Compact.TEntry", [("Entry.field", {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})])
    style.layout("Compact.TCombobox", [("Combobox.field", {"sticky": "nsew", "children": [
        ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
        ("Combobox.padding", {"sticky": "nsew", "children": [("Combobox.textarea", {"sticky": "nsew"})]})]})])


def _check_indicators(root, style, scale):
    images = []
    for selected, disabled in ((False, False), (True, False), (False, True), (True, True)):
        p = Painter(round(26 * scale), round(20 * scale), scale, FIELD)
        fill = ("#777331" if disabled else ACCENT) if selected else "#303030"
        p.box((1, 1, 19, 19), 5, fill=fill, outline="#757575" if disabled else "#a0a0a0")
        if selected:
            p.line((5, 10, 9, 14, 16, 6), BG, 3)
        images.append(ImageTk.PhotoImage(p.finish(), master=root))
    root._game_images.extend(images)
    off, on, disabled_off, disabled_on = images
    style.element_create("Game.Check.indicator", "image", off, ("disabled", "selected", disabled_on),
                         ("disabled", disabled_off), ("selected", on))
    for name in ("TCheckbutton", "Card.TCheckbutton"):
        style.configure(name, background=FIELD, foreground=TEXT, padding=(0, round(4 * scale)))
        style.map(name, background=[("active", FIELD)], foreground=[("disabled", "#757575")])
        style.layout(name, [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Game.Check.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]})]})])


def _window_identity(root):
    icon = Image.new("RGBA", (64, 64), ORANGE)
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((9, 6, 54, 58), radius=8, fill=BG, outline="#dddddd", width=3)
    draw.rounded_rectangle((17, 13, 47, 35), radius=4, fill="#91b3b2")
    draw.line(((22, 23), (29, 29), (41, 17)), fill=BG, width=4)
    draw.line(((17, 46), (29, 46)), fill=TEXT, width=3)
    draw.line(((23, 40), (23, 52)), fill=TEXT, width=3)
    draw.ellipse((40, 43, 47, 50), fill=ORANGE)
    root._autozzz_icon = ImageTk.PhotoImage(icon, master=root)
    root.iconphoto(True, root._autozzz_icon)

    def dark_titlebar():
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes
        try:
            user32 = ctypes.windll.user32
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            hwnd = user32.GetParent(root.winfo_id())
            enabled = ctypes.c_int(1)
            for attribute in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)) == 0:
                    break
        except (AttributeError, OSError):
            pass
    token = root.after(100, dark_titlebar)
    root.bind("<Destroy>", lambda event: root.after_cancel(token) if event.widget is root else None, add="+")


def page_heading(parent, title, english, hint, scale):
    d = lambda n: round(n * scale)
    frame = ttk.Frame(parent)
    frame.grid(row=0, column=0, sticky="ew", pady=(0, d(10)))
    line = ttk.Frame(frame)
    line.pack(fill="x")
    ttk.Label(line, text=title, style="PageTitle.TLabel").pack(side="left")
    ttk.Label(line, text=" / " + english, style="Kicker.TLabel").pack(side="left", padx=d(12))
    wrapped_label(frame, text=hint, style="Hint.TLabel").pack(fill="x", pady=(d(4), 0))


def card(parent, scale, *, padding=16):
    return ttk.Frame(parent, style="Card.TFrame", padding=round(padding * scale))


def wrapped_label(parent, *, text=None, textvariable=None, style="CardHint.TLabel", **kwargs):
    label = ttk.Label(parent, text=text, textvariable=textvariable, style=style, **kwargs)
    label.bind("<Configure>", lambda event: label.configure(wraplength=max(80, event.width)))
    return label
