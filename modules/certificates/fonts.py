from pathlib import Path

# Bundled preset fonts (see fonts/LICENSE.txt) -- boxes pick a name from this
# registry rather than uploading their own font file.

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

BUNDLED_FONTS = {
    "sans-regular": FONTS_DIR / "DejaVuSans.ttf",
    "sans-bold": FONTS_DIR / "DejaVuSans-Bold.ttf",
    "serif-regular": FONTS_DIR / "DejaVuSerif.ttf",
}

DEFAULT_FONT = "sans-regular"

FONT_CHOICES = [(name, name) for name in BUNDLED_FONTS]
