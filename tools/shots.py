"""Write review screenshots into shots/: every sample in every page theme, plus side-by-side sheets.

Needs the server on 127.0.0.1:8766 and playwright; the side-by-side sheets in shots/compare/ also need Pillow.
Screenshots are JPEG: the parchment texture of the sketch theme makes PNGs several MB each.
Usage: python3 tools/shots.py [theme ...]
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "shots"
# (source, steps to take before the shot, extra query)
SAMPLES = [
    ("transformer.content.json", 9, ""),
    ("transformer.content.json", 3, "&display=lesson"),
    ("claude-code-memory.content.json", 12, ""),
    ("autoresearch.content.json", 3, ""),
    ("tcp-handshake.content.json", 2, ""),
    ("agent-message.content.json", 3, ""),
    ("chart-demo.content.json", 2, ""),
]
LABELS = {"book": "book · 书页", "product": "product · 产品", "sketch": "sketch · 手绘"}


def shot_name(src: str, extra: str) -> str:
    return src.replace(".content.json", "") + extra.replace("&display=", "-")


async def main(themes):
    OUT.mkdir(exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for theme in themes:
            for src, steps, extra in SAMPLES:
                pg = await b.new_page(viewport={"width": 1600, "height": 1000})
                await pg.goto(f"http://127.0.0.1:{server.PORT}/?src={src}&theme={theme}{extra}")
                await pg.wait_for_selector("body[data-renderer]", timeout=20000)
                await pg.wait_for_timeout(1500)
                for _ in range(steps):
                    await pg.click("#next")
                    await pg.wait_for_timeout(250)
                await pg.mouse.move(2, 2)
                await pg.wait_for_timeout(1400)
                path = OUT / f"{theme}-{shot_name(src, extra)}.jpg"
                await pg.screenshot(path=str(path), full_page=True, type="jpeg", quality=85)
                await pg.close()
        await b.close()


def compare_sheets():
    """One image per sample: the three themes side by side."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Pillow not installed: skipped shots/compare/")
        return
    font = ImageFont.load_default()
    for face in ("/System/Library/Fonts/Hiragino Sans GB.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        try:
            font = ImageFont.truetype(face, 40)
            break
        except OSError:
            continue
    (OUT / "compare").mkdir(exist_ok=True)
    for src, _, extra in SAMPLES:
        name = shot_name(src, extra)
        files = [OUT / f"{t}-{name}.jpg" for t in LABELS]
        if not all(f.exists() for f in files):
            continue
        w, pad, head = 1000, 24, 84
        ims = [Image.open(f).convert("RGB") for f in files]
        ims = [im.resize((w, int(im.height * w / im.width)), Image.LANCZOS) for im in ims]
        sheet = Image.new("RGB", (w * 3 + pad * 4, max(im.height for im in ims) + head + pad), "#e9e9ec")
        draw = ImageDraw.Draw(sheet)
        for i, (label, im) in enumerate(zip(LABELS.values(), ims)):
            x = pad + i * (w + pad)
            draw.text((x, 22), label, fill="#222", font=font)
            sheet.paste(im, (x, head))
        sheet.save(OUT / "compare" / f"{name}.jpg", quality=85, optimize=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or list(server.THEMES)))
    compare_sheets()
