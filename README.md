# Typerr — Tier-Tippspiel

A typing game for kids learning the keyboard. An animal name is shown
letter by letter: the next letter appears "see-through", and the child
simply presses that key — no input box, no Enter. A wrong key makes the
letter wiggle. When the word is complete, the picture pops in with a
gentle breathe-and-bounce animation, and the next word starts with
**▶ Weiter** (or Enter/Space). If nothing is typed for 30 seconds, a
short hint text appears.

Words can be typed in **German or English** (F2 switches). Comes with
131 words — animals and food — as flat-color SVG icons (from
[svgrepo.com](https://www.svgrepo.com)).

## Controls

| Key | Action |
|-----|--------|
| any letter | type the see-through letter |
| Enter / Space | next word (after the reveal) |
| F1 | help |
| F2 | switch language (Deutsch / English) |
| F9 | dark / light mode |
| F11 | fullscreen (Esc leaves it) |

## Running from source

Requirements on Ubuntu (only system packages, no pip/venv needed):

```bash
sudo apt install python3-tk python3-pil imagemagick
```

Then:

```bash
./typing_game.py        # or: python3 typing_game.py
```

- `words.csv` holds the word list: `filename,english,german`, one animal
  per line. The SVG files live in `svg/`.
- On first use of a word, its SVG is rasterized once with ImageMagick
  into `.cache_png/` (tkinter cannot display SVGs directly). After that,
  ImageMagick is no longer needed.
- Adding an animal = drop an SVG into `svg/` and add a line to
  `words.csv`.

## Building the single-file executable

The game can be bundled into one self-contained binary (Python, tkinter,
PIL, word list and all pre-rendered images included) with
[PyInstaller](https://pyinstaller.org):

```bash
# one-time setup
python3 -m pip install --user --break-system-packages pyinstaller

# make sure every image is pre-rendered, so the binary
# does not need ImageMagick on the target machine
for f in svg/*.svg; do
  n=$(basename "$f" .svg)
  [ -f ".cache_png/$n.png" ] || convert -background none "$f" -resize 512x512 ".cache_png/$n.png"
done

# build
~/.local/bin/pyinstaller --onefile --windowed --name typerr \
  --add-data "words.csv:." \
  --add-data ".cache_png:.cache_png" \
  typing_game.py
```

The result is `dist/typerr` (~18 MB) — a single file that runs on other
x86-64 Ubuntu machines (same release or newer) with no installation:

```bash
chmod +x typerr   # if the executable bit got lost in transfer
./typerr
```

Because `words.csv` is baked into the binary, changing the word list
means rebuilding. `build/` and `typerr.spec` are PyInstaller work files
and can be deleted.

### AppImage (optional)

For a distro-independent single file with desktop integration, the
PyInstaller output can be wrapped into an AppImage with
[appimagetool](https://github.com/AppImage/appimagetool): put
`dist/typerr`, a `.desktop` file and an icon into an `AppDir/` and run
`appimagetool AppDir/`. Not set up in this repo — the plain PyInstaller
binary is usually enough for sharing between Ubuntu machines.
