#!/usr/bin/env python3
"""Typerr — an animal word typing game for kids learning the keyboard.

The animal name is shown letter by letter: the next letter to type
appears semi-transparent (ghosted), letters still to come are underscores.
The child presses the ghosted key directly — no input box, no Enter.
A wrong key shakes the ghost letter. When the word is complete the animal
picture is revealed with a gentle breathe-and-bounce animation, then a
big "Weiter"/"Next" button (or Enter/Space) advances. A short hint text
appears only after 30 seconds without typing.

Shortcuts: F1 help, F2 language (Deutsch/English), F9 dark/light,
F11 fullscreen.

Words come from words.csv next to this script:
    filename,english,german
    lion.svg,Lion,Löwe

SVG files live in the svg/ directory. They are rasterized once into
.cache_png/ using ImageMagick's `convert`; animation frames are scaled
with PIL and handed to tkinter as PNG bytes, so no python3-pil.imagetk
is needed.
"""

import csv
import io
import math
import os
import random
import shutil
import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

try:
    from PIL import Image
except ImportError:
    Image = None

# ---------------------------------------------------------------- themes

THEMES = {
    "dark": {
        "BG": "#1e1e2e",        # window background
        "CARD": "#2a2a3d",      # card / button background
        "FG": "#f8f8f2",        # main text
        "ACCENT": "#8be9fd",    # typed letters
        "GOOD": "#50fa7b",      # success
        "BAD": "#ff5555",       # error
        "MUTED": "#9399b2",     # secondary text
        "GHOST": "#5a5d7a",     # the "transparent" letter to type next
        "FUTURE": "#3a3d55",    # underscores for letters still to come
    },
    "light": {
        "BG": "#f4f4fb",
        "CARD": "#e3e4f0",
        "FG": "#22223a",
        "ACCENT": "#0b7ca6",
        "GOOD": "#1e9e4f",
        "BAD": "#d64545",
        "MUTED": "#6b6e85",
        "GHOST": "#b9bbd2",
        "FUTURE": "#d9dbe8",
    },
}
T = dict(THEMES["dark"])        # active palette (mutated on toggle)


def set_theme(name):
    T.clear()
    T.update(THEMES[name])


WORDS_FILE = "words.csv"
SVG_DIR = "svg"
CACHE_DIR = ".cache_png"
ALPHA = 0.93            # window transparency (1.0 = opaque)

RASTER_SIZE = 512       # base rasterization size for the SVGs
BASE_W, BASE_H = 760, 680   # design size; the UI scales relative to this
IMAGE_SIZE = 260        # nominal on-screen image size at design size
CANVAS_H = 300          # picture area height at design size
WORD_CANVAS_H = 100
SCALE_AMP = 0.035       # ±3.5 % breathing
BOUNCE_AMP = 10         # px bounce height
FRAME_MS = 40           # ~25 fps
PHASE_STEP = 0.14       # radians per frame -> ~1.8 s per cycle

HINT_DELAY_MS = 30000   # show the hint after this much typing inactivity

LANG = {
    "de": {
        "typed": "german",
        "hint": "Tippe den durchsichtigen Buchstaben!",
        "next": "▶  Weiter",
        "done_title": "\U0001F389 Geschafft! \U0001F389",
        "summary": "Wörter geschafft: {}\nFalsche Tasten: {}",
        "again": "Nochmal spielen", "quit": "Beenden",
        "help_title": "Hilfe",
        "help_text": (
            "So funktioniert's:\n"
            "\n"
            "Tippe den durchsichtigen Buchstaben auf der Tastatur.\n"
            "Buchstabe für Buchstabe entsteht so das Tierwort.\n"
            "Bei einer falschen Taste wackelt der Buchstabe.\n"
            "Ist das Wort fertig, erscheint das Tier — mit ▶ Weiter,\n"
            "Enter oder der Leertaste geht es zum nächsten Wort.\n"
            "\n"
            "Tastenkürzel:\n"
            "F1\tHilfe anzeigen\n"
            "F2\tSprache wechseln (Deutsch / English)\n"
            "F9\tDunkel / Hell\n"
            "F11\tVollbild an/aus"),
        "close": "Schließen",
    },
    "en": {
        "typed": "english",
        "hint": "Type the see-through letter!",
        "next": "▶  Next",
        "done_title": "\U0001F389 All done! \U0001F389",
        "summary": "Words completed: {}\nWrong keys: {}",
        "again": "Play again", "quit": "Quit",
        "help_title": "Help",
        "help_text": (
            "How it works:\n"
            "\n"
            "Type the see-through letter on your keyboard.\n"
            "Letter by letter the animal word appears.\n"
            "A wrong key makes the letter wiggle.\n"
            "When the word is complete the animal appears — press\n"
            "▶ Next, Enter or Space to go to the next word.\n"
            "\n"
            "Shortcuts:\n"
            "F1\tShow help\n"
            "F2\tSwitch language (Deutsch / English)\n"
            "F9\tDark / Light\n"
            "F11\tFullscreen on/off"),
        "close": "Close",
    },
}


def resource_dir():
    """Directory holding words.csv, svg/ and .cache_png/.

    Inside a PyInstaller one-file binary the data is unpacked to a
    temporary directory exposed as sys._MEIPASS.
    """
    return (getattr(sys, "_MEIPASS", None)
            or os.path.dirname(os.path.abspath(__file__)))


def load_words(path):
    """Read (filename, english, german) rows; skip header and bad rows."""
    entries = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) < 3:
                    continue
                filename, english, german = (c.strip() for c in row[:3])
                if filename.lower() == "filename":
                    continue  # header
                if not german:
                    continue
                entries.append({"file": filename, "english": english,
                                "german": german})
    except FileNotFoundError:
        return None
    return entries


class AnimalImage:
    """Serves breathe-animation frames; rasterizes the SVG if uncached."""

    def __init__(self, filename, svg_dir, cache_dir):
        self.base = None            # PIL image at RASTER_SIZE
        self.frames = {}            # pixel size -> tk.PhotoImage
        if Image is None:
            return
        stem = os.path.splitext(os.path.basename(filename))[0]
        png_path = os.path.join(cache_dir, stem + ".png")
        if not os.path.isfile(png_path):
            svg_path = os.path.join(svg_dir, filename)
            if not os.path.isfile(svg_path):
                print(f"warning: no image for {filename}", file=sys.stderr)
                return
            os.makedirs(cache_dir, exist_ok=True)
            try:
                subprocess.run(
                    ["convert", "-background", "none", svg_path,
                     "-resize", f"{RASTER_SIZE}x{RASTER_SIZE}", png_path],
                    check=True, capture_output=True)
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                print(f"warning: could not rasterize {svg_path}: {e}",
                      file=sys.stderr)
                return
        try:
            self.base = Image.open(png_path).convert("RGBA")
        except OSError as e:
            print(f"warning: could not load {png_path}: {e}",
                  file=sys.stderr)

    def frame(self, scale, nominal=IMAGE_SIZE):
        """tk.PhotoImage for the given scale factor (cached by pixel size)."""
        if self.base is None:
            return None
        longest = max(self.base.size)
        target = max(1, round(nominal * scale))
        size = (max(1, self.base.width * target // longest),
                max(1, self.base.height * target // longest))
        if size not in self.frames:
            buf = io.BytesIO()
            self.base.resize(size, Image.LANCZOS).save(buf, "PNG")
            self.frames[size] = tk.PhotoImage(data=buf.getvalue())
        return self.frames[size]


class WordDisplay:
    """Per-letter word rendering on a canvas: typed / ghost / upcoming."""

    def __init__(self, canvas):
        self.canvas = canvas
        self.scale = 1.0
        self.word = ""
        self.pos = 0            # index of the letter to type next
        self.revealed = False
        self.items = []         # one canvas text item per character
        self.xs = []            # resting x of each item
        self.shake_job = None
        canvas.bind("<Configure>", lambda e: self._layout())

    def set_word(self, word):
        self._cancel_shake()
        self.word = word
        self.pos = 0
        self.revealed = False
        self._layout()

    def _font(self):
        base = 52 if len(self.word) <= 9 else (40 if len(self.word) <= 13
                                               else 30)
        return tkfont.Font(family="DejaVu Sans Mono",
                           size=max(8, int(base * self.scale)),
                           weight="bold")

    def _layout(self):
        self._cancel_shake()
        self.canvas.delete("all")
        self.items, self.xs = [], []
        if not self.word:
            return
        font = self._font()
        cw = font.measure("M") + max(4, int(6 * self.scale))
        width = self.canvas.winfo_width()
        if width <= 1:
            width = self.canvas.winfo_reqwidth()
        x = (width - cw * len(self.word)) / 2 + cw / 2
        y = self.canvas.winfo_height() / 2 or WORD_CANVAS_H / 2
        for i, _ in enumerate(self.word):
            item = self.canvas.create_text(x, y, text="", font=font)
            self.items.append(item)
            self.xs.append(x)
            x += cw
        self.repaint()

    @staticmethod
    def _disp(ch):
        return ch.upper()

    def _paint(self):
        for i, ch in enumerate(self.word):
            item = self.items[i]
            if i < self.pos:                       # already typed
                self.canvas.itemconfig(item, text=self._disp(ch),
                                       fill=T["ACCENT"])
            elif i == self.pos:                    # type me — "transparent"
                shown = "␣" if ch == " " else self._disp(ch)
                self.canvas.itemconfig(item, text=shown, fill=T["GHOST"])
            else:                                  # still hidden
                shown = "" if ch == " " else "_"
                self.canvas.itemconfig(item, text=shown, fill=T["FUTURE"])

    def repaint(self):
        """Redraw with current progress and palette."""
        if self.revealed:
            for i, ch in enumerate(self.word):
                self.canvas.itemconfig(self.items[i], text=self._disp(ch),
                                       fill=T["GOOD"])
        else:
            self._paint()

    def advance(self):
        self.pos += 1
        self._paint()

    def finished(self):
        return self.pos >= len(self.word)

    def current_char(self):
        return self.word[self.pos] if self.pos < len(self.word) else None

    def reveal_all(self):
        self._cancel_shake()
        self.pos = len(self.word)
        self.revealed = True
        self.repaint()

    # -------------------------------------------------- wrong-key shake

    def shake(self):
        if self.pos >= len(self.items):
            return
        self._cancel_shake()
        item = self.items[self.pos]
        self.canvas.itemconfig(item, fill=T["BAD"])
        self._shake_step(item, self.xs[self.pos], 6)

    def _shake_step(self, item, base_x, times):
        if times <= 0:
            _, y = self.canvas.coords(item)
            self.canvas.coords(item, base_x, y)
            self.canvas.itemconfig(item, fill=T["GHOST"])
            self.shake_job = None
            return
        _, y = self.canvas.coords(item)
        offset = 5 if times % 2 else -5
        self.canvas.coords(item, base_x + offset, y)
        self.shake_job = self.canvas.after(
            45, self._shake_step, item, base_x, times - 1)

    def _cancel_shake(self):
        if self.shake_job is not None:
            self.canvas.after_cancel(self.shake_job)
            self.shake_job = None


class TypingGame(tk.Tk):
    def __init__(self, entries, svg_dir, cache_dir):
        super().__init__()
        self.title("Typerr — Tier-Tippspiel")
        self.configure(bg=T["BG"])
        self.geometry("760x680")
        self.minsize(620, 560)
        try:
            self.attributes("-alpha", ALPHA)
        except tk.TclError:
            pass  # no compositor — stay opaque

        self.entries = entries
        self.svg_dir = svg_dir
        self.cache_dir = cache_dir
        self.index = 0
        self.total_wrong = 0
        self.state = "typing"   # typing | revealing | done
        self.image = None
        self.anim_phase = 0.0
        self.anim_job = None
        self.fullscreen = False
        self.ui_scale = 1.0
        self.lang = "de"
        self.theme = "dark"
        self.help_win = None
        self.hint_job = None

        self._build_ui()
        self.bind("<Key>", self._on_key)
        self.bind("<F1>", self._show_help)
        self.bind("<F2>", self._toggle_lang)
        self.bind("<F9>", self._toggle_theme)
        self.bind("<F11>", self._toggle_fullscreen)
        self.bind("<Escape>", self._leave_fullscreen)
        self.bind("<Configure>", self._on_resize)
        self.focus_set()
        random.shuffle(self.entries)
        self._show_word()

    # ------------------------------------------------------------ UI

    def _build_ui(self):
        # named fonts so everything rescales with the window
        self.f_top = tkfont.Font(family="DejaVu Sans", size=14)
        self.f_button = tkfont.Font(family="DejaVu Sans", size=24,
                                    weight="bold")
        self.f_hint = tkfont.Font(family="DejaVu Sans", size=13)

        self.topbar = tk.Frame(self)
        self.topbar.pack(fill="x", padx=24, pady=(18, 0))

        def topbtn(text, command):
            return tk.Button(self.topbar, text=text, font=self.f_top,
                             relief="flat", padx=12, pady=2, cursor="hand2",
                             takefocus=0, command=command)

        self.lang_btn = topbtn("🌐 DE", self._toggle_lang)
        self.lang_btn.pack(side="right")
        self.theme_btn = topbtn("☀", self._toggle_theme)
        self.theme_btn.pack(side="right", padx=(0, 8))
        self.help_btn = topbtn("?", self._show_help)
        self.help_btn.pack(side="right", padx=(0, 8))

        # animal picture area
        self.canvas = tk.Canvas(self, height=CANVAS_H, highlightthickness=0)
        self.canvas.pack(fill="x", pady=(12, 0))
        self.canvas_img = None

        # per-letter word display
        self.word_canvas = tk.Canvas(self, height=WORD_CANVAS_H,
                                     highlightthickness=0)
        self.word_canvas.pack(fill="x", pady=(6, 0))
        self.word = WordDisplay(self.word_canvas)

        self.next_btn = tk.Button(self, text=LANG[self.lang]["next"],
                                  font=self.f_button, relief="flat",
                                  padx=36, pady=10, cursor="hand2",
                                  takefocus=0, command=self._next_word)

        # empty until the idle timer fires, so the layout never jumps
        self.hint_lbl = tk.Label(self, text="", font=self.f_hint)
        self.hint_lbl.pack(side="bottom", pady=(4, 18))

        self._apply_theme()

    def _apply_theme(self):
        self.configure(bg=T["BG"])
        self.topbar.configure(bg=T["BG"])
        self.hint_lbl.configure(bg=T["BG"], fg=T["MUTED"])
        for btn in (self.lang_btn, self.theme_btn, self.help_btn):
            btn.configure(bg=T["CARD"], fg=T["FG"],
                          activebackground=T["BG"],
                          activeforeground=T["ACCENT"])
        self.canvas.configure(bg=T["BG"])
        self.word_canvas.configure(bg=T["BG"])
        self.next_btn.configure(bg=T["GOOD"], fg=T["BG"],
                                activebackground=T["ACCENT"],
                                activeforeground=T["BG"])
        self.theme_btn.configure(text="☀" if self.theme == "dark" else "🌙")
        self.word.repaint()

    # ------------------------------------------------------------ toggles

    def _toggle_theme(self, _event=None):
        self.theme = "light" if self.theme == "dark" else "dark"
        set_theme(self.theme)
        self._apply_theme()
        if self.state == "done":
            self.summary.destroy()
            self._finish()  # rebuild the summary in the new palette
        if self.help_win is not None and self.help_win.winfo_exists():
            self.help_win.destroy()
            self._show_help()

    def _toggle_lang(self, _event=None):
        if self.state == "done":
            return  # summary screen is already built; switch next round
        self.lang = "en" if self.lang == "de" else "de"
        strings = LANG[self.lang]
        self.lang_btn.config(text=f"🌐 {self.lang.upper()}")
        self.next_btn.config(text=strings["next"])
        if self.anim_job is not None:
            self.after_cancel(self.anim_job)
            self.anim_job = None
        if self.help_win is not None and self.help_win.winfo_exists():
            self.help_win.destroy()
            self._show_help()
        self._show_word()  # restart the current word in the new language

    def _toggle_fullscreen(self, _event=None):
        self.fullscreen = not self.fullscreen
        self.attributes("-fullscreen", self.fullscreen)

    def _leave_fullscreen(self, _event=None):
        if self.fullscreen:
            self.fullscreen = False
            self.attributes("-fullscreen", False)

    # ------------------------------------------------------------ help

    def _show_help(self, _event=None):
        if self.help_win is not None and self.help_win.winfo_exists():
            self.help_win.destroy()  # F1 again closes it
            self.help_win = None
            return
        strings = LANG[self.lang]
        win = tk.Toplevel(self)
        self.help_win = win
        win.title(strings["help_title"])
        win.configure(bg=T["BG"], padx=30, pady=24)
        win.transient(self)
        win.resizable(False, False)
        tk.Label(win, text=strings["help_title"],
                 font=("DejaVu Sans", int(20 * self.ui_scale), "bold"),
                 bg=T["BG"], fg=T["ACCENT"]).pack(anchor="w", pady=(0, 12))
        tk.Label(win, text=strings["help_text"],
                 font=("DejaVu Sans", int(13 * self.ui_scale)),
                 bg=T["BG"], fg=T["FG"], justify="left",
                 anchor="w").pack(anchor="w")
        tk.Button(win, text=strings["close"],
                  font=("DejaVu Sans", int(13 * self.ui_scale), "bold"),
                  bg=T["CARD"], fg=T["FG"], activebackground=T["BG"],
                  activeforeground=T["ACCENT"], relief="flat",
                  padx=16, pady=4, takefocus=0,
                  command=win.destroy).pack(pady=(18, 0))
        win.bind("<F1>", lambda e: win.destroy())
        win.bind("<Escape>", lambda e: win.destroy())

    # ------------------------------------------------------------ resize

    def _on_resize(self, event):
        if event.widget is not self:
            return  # a child widget's Configure event
        s = min(event.width / BASE_W, event.height / BASE_H)
        s = max(0.7, min(s, 3.0))
        if abs(s - self.ui_scale) < 0.02:
            return
        self.ui_scale = s
        self.f_top.configure(size=int(14 * s))
        self.f_button.configure(size=int(24 * s))
        self.f_hint.configure(size=int(13 * s))
        self.canvas.config(height=int(CANVAS_H * s))
        self.word_canvas.config(height=int(WORD_CANVAS_H * s))
        self.word.scale = s
        self.word._layout()

    # ------------------------------------------------------------ game flow

    # hint appears only after HINT_DELAY_MS without any typing
    def _arm_hint(self):
        self._disarm_hint()
        self.hint_job = self.after(HINT_DELAY_MS, self._show_hint)

    def _disarm_hint(self):
        if self.hint_job is not None:
            self.after_cancel(self.hint_job)
            self.hint_job = None
        self.hint_lbl.config(text="")

    def _show_hint(self):
        self.hint_job = None
        if self.state == "typing":
            self.hint_lbl.config(text=LANG[self.lang]["hint"])

    def _show_word(self):
        entry = self.entries[self.index]
        self.state = "typing"
        self.anim_phase = 0.0
        self.image = AnimalImage(entry["file"], self.svg_dir,
                                 self.cache_dir)
        self.canvas.delete("all")
        self.canvas_img = None
        # ß is displayed and typed as double-s (STRAUSS), kid-friendly
        strings = LANG[self.lang]
        self.word.set_word(entry[strings["typed"]].replace("ß", "ss"))
        self.next_btn.pack_forget()
        self._arm_hint()

    def _on_key(self, event):
        if self.state == "revealing":
            if event.keysym in ("Return", "KP_Enter", "space"):
                self._next_word()
            return
        if self.state != "typing":
            return
        ch = event.char
        if len(ch) != 1 or not ch.isprintable():
            return  # modifiers, arrows, F-keys, escape, ...
        target = self.word.current_char()
        if target is None:
            return
        self._arm_hint()  # typing activity hides the hint and resets 30 s
        if ch.lower() == target.lower():
            self.word.advance()
            if self.word.finished():
                self._correct()
        else:
            self.total_wrong += 1
            self.word.shake()

    def _correct(self):
        self.state = "revealing"
        self._disarm_hint()
        self.word.reveal_all()
        self.next_btn.pack(pady=(10, 20))
        self._animate()

    def _animate(self):
        """Gentle sinus breathe (scale) + bounce (y) of the animal image."""
        if self.state != "revealing" or self.image is None:
            return
        scale = 1.0 + SCALE_AMP * math.sin(self.anim_phase)
        y_off = (-BOUNCE_AMP * self.ui_scale
                 * abs(math.sin(self.anim_phase / 2)))
        frame = self.image.frame(scale, int(IMAGE_SIZE * self.ui_scale))
        if frame is not None:
            cx = self.canvas.winfo_width() // 2 or self.winfo_width() // 2
            cy = (self.canvas.winfo_height() or CANVAS_H) // 2 + int(y_off)
            if self.canvas_img is None:
                self.canvas_img = self.canvas.create_image(cx, cy,
                                                           image=frame)
            else:
                self.canvas.itemconfig(self.canvas_img, image=frame)
                self.canvas.coords(self.canvas_img, cx, cy)
            self._frame_ref = frame  # keep a reference alive
        self.anim_phase += PHASE_STEP
        self.anim_job = self.after(FRAME_MS, self._animate)

    def _next_word(self):
        if self.state != "revealing":
            return
        if self.anim_job is not None:
            self.after_cancel(self.anim_job)
            self.anim_job = None
        self.index += 1
        if self.index >= len(self.entries):
            self._finish()
        else:
            self._show_word()

    # ------------------------------------------------------------ end game

    def _finish(self):
        self.state = "done"
        self._disarm_hint()
        for widget in (self.canvas, self.word_canvas, self.hint_lbl,
                       self.next_btn):
            widget.pack_forget()

        total = len(self.entries)
        self.summary = tk.Frame(self, bg=T["BG"])
        self.summary.pack(expand=True)

        s = self.ui_scale
        strings = LANG[self.lang]
        tk.Label(self.summary, text=strings["done_title"],
                 font=("DejaVu Sans", int(36 * s), "bold"),
                 bg=T["BG"], fg=T["GOOD"]).pack(pady=(0, 16))
        tk.Label(self.summary,
                 text=strings["summary"].format(total, self.total_wrong),
                 font=("DejaVu Sans", int(18 * s), "bold"),
                 bg=T["BG"], fg=T["FG"],
                 justify="center").pack(pady=(0, 20))

        btns = tk.Frame(self.summary, bg=T["BG"])
        btns.pack()
        tk.Button(btns, text=strings["again"],
                  font=("DejaVu Sans", int(16 * s), "bold"),
                  bg=T["CARD"], fg=T["GOOD"], activebackground=T["BG"],
                  activeforeground=T["GOOD"], relief="flat", padx=16, pady=6,
                  takefocus=0, command=self._restart).pack(side="left",
                                                           padx=8)
        tk.Button(btns, text=strings["quit"],
                  font=("DejaVu Sans", int(16 * s)),
                  bg=T["CARD"], fg=T["BAD"], activebackground=T["BG"],
                  activeforeground=T["BAD"], relief="flat", padx=16, pady=6,
                  takefocus=0, command=self.destroy).pack(side="left",
                                                          padx=8)

    def _restart(self):
        self.summary.destroy()
        random.shuffle(self.entries)
        self.index = 0
        self.total_wrong = 0
        self.canvas.pack(fill="x", pady=(12, 0))
        self.word_canvas.pack(fill="x", pady=(6, 0))
        self.hint_lbl.pack(side="bottom", pady=(4, 18))
        self._show_word()


def fatal(message):
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Typerr", message)
    sys.exit(1)


def main():
    here = resource_dir()
    entries = load_words(os.path.join(here, WORDS_FILE))
    if entries is None:
        fatal(f"Could not find {WORDS_FILE} next to the script.\n"
              "Format: filename,english,german — one animal per line.")
    if not entries:
        fatal(f"{WORDS_FILE} contains no usable rows.\n"
              "Format: filename,english,german — one animal per line.")
    if Image is None:
        print("warning: PIL not available (sudo apt install python3-pil) — "
              "running without pictures", file=sys.stderr)
    elif shutil.which("convert") is None:
        print("warning: ImageMagick not available (sudo apt install "
              "imagemagick) — running without pictures", file=sys.stderr)

    TypingGame(entries, os.path.join(here, SVG_DIR),
               os.path.join(here, CACHE_DIR)).mainloop()


if __name__ == "__main__":
    main()
