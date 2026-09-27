"""Regenerate the README images from demo mode (fake data only).

  py tools/make_media.py

Writes docs/demo.gif, docs/screenshot-files.png, docs/screenshot-gmail.png,
docs/banner.png and docs/social-preview.png. Needs Pillow.
"""

import runpy
import sys
import time
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageGrab

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DOCS.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
sys.argv.append("--demo")  # the app reads this at import time

BG, ORANGE, CYAN, MAGENTA = (5, 5, 5), (255, 130, 0), (0, 240, 255), (255, 42, 109)
FONT_DIR = Path("C:/Windows/Fonts")


def font(size, bold=True):
    name = "consolab.ttf" if bold else "consola.ttf"
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default()


def record_app():
    mod = runpy.run_path(str(ROOT / "app.pyw"), run_name="app")
    root = tk.Tk()
    app = mod["App"](root)
    root.geometry("1080x860+40+40")
    root.attributes("-topmost", True)
    frames = []

    def grab():
        root.update()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        return ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height()),
                              all_screens=True)

    def tick():
        frames.append((time.time(), grab()))
        root.after(100, tick)

    def idle():
        return app.state.cget("text").startswith("IDLE")

    def when_idle(then, hold=1.5):
        def check():
            if idle():
                root.after(int(hold * 1000), then)
            else:
                root.after(150, check)
        root.after(400, check)

    def step_files():
        app.run(app.tidy_args())
        when_idle(shot_files)

    def shot_files():
        grab().save(DOCS / "screenshot-files.png")
        app.show_tab("GMAIL")
        root.after(700, lambda: (app.run(["gmail_tidy.py", "scan"]), when_idle(shot_gmail)))

    def shot_gmail():
        grab().save(DOCS / "screenshot-gmail.png")
        root.destroy()

    root.after(800, tick)
    root.after(1500, step_files)
    root.mainloop()
    return frames


def save_gif(frames):
    small = [f.convert("RGB").resize((f.width * 6 // 10, f.height * 6 // 10), Image.LANCZOS)
             for _, f in frames]
    # screen grabs are slower than the timer, so use real gaps between frames
    durations = [int((b[0] - a[0]) * 1000) for a, b in zip(frames, frames[1:])] + [2500]
    small[0].save(DOCS / "demo.gif", save_all=True, append_images=small[1:],
                  duration=durations, loop=0, optimize=True)


def scanlines(img):
    d = ImageDraw.Draw(img, "RGBA")
    for y in range(0, img.height, 4):
        d.line([(0, y), (img.width, y)], fill=(0, 0, 0, 60))


def draw_title(d, x, y, size):
    title = "TIDY//OS"
    f = font(size)
    for off, col in ((4, MAGENTA), (-4, CYAN)):  # chromatic "glitch" offset
        d.text((x + off, y), title, font=f, fill=col)
    d.text((x, y), title, font=f, fill=ORANGE)


def make_banner():
    img = Image.new("RGB", (1280, 320), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([8, 8, 1271, 311], outline=ORANGE, width=3)
    draw_title(d, 60, 50, 150)
    d.text((66, 230), ">> personal system janitor  //  nothing gets deleted, only sorted",
           font=font(26, bold=False), fill=CYAN)
    scanlines(img)
    img.save(DOCS / "banner.png")


def make_social_preview():
    img = Image.new("RGB", (1280, 640), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 1269, 629], outline=ORANGE, width=4)
    draw_title(d, 60, 90, 120)
    d.text((66, 250), "Sorts your PC + Gmail.", font=font(40), fill=CYAN)
    d.text((66, 305), "Never deletes a thing.", font=font(40), fill=MAGENTA)
    for i, line in enumerate(["> preview first", "> undo everything", "> flags Potential Junk",
                              "> neon control panel"]):
        d.text((66, 400 + i * 44), line, font=font(28, bold=False), fill=ORANGE)
    shot = Image.open(DOCS / "screenshot-files.png").convert("RGB")
    shot.thumbnail((600, 480))
    img.paste(shot, (640, 80))
    d.rectangle([636, 76, 640 + shot.width + 3, 80 + shot.height + 3], outline=ORANGE, width=3)
    scanlines(img)
    img.save(DOCS / "social-preview.png")


if __name__ == "__main__":
    frames = record_app()
    print(f"captured {len(frames)} frames")
    save_gif(frames)
    make_banner()
    make_social_preview()
    for p in sorted(DOCS.iterdir()):
        print(f"  {p.name:24} {p.stat().st_size / 1e6:.1f} MB")
