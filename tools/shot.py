"""Debug screenshot helper: python3 tools/shot.py SRC OUT_PREFIX [clicks...]"""
import asyncio, sys
from playwright.async_api import async_playwright
async def main(src, out, clicks):
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width":1000,"height":1200})
        await pg.goto("http://127.0.0.1:8766/?src=" + src)
        try: await pg.wait_for_selector("body[data-renderer]", timeout=15000)
        except Exception as e: print("no renderer attr")
        print("renderer", await pg.evaluate("document.body.dataset.renderer"))
        print(await pg.evaluate("[...document.querySelectorAll('#diagram path.cv-edge')].map(p=>p.id+'|'+p.getAttribute('data-edge-key')).join('\\n')"))
        print(await pg.evaluate("[...document.querySelectorAll('#diagram .edgeLabel')].map(p=>p.getAttribute('data-edge-key')).join(',')"))
        await pg.wait_for_timeout(400)
        await pg.screenshot(path=f"{out}-0.png", full_page=True)
        for i in range(int(clicks)):
            await pg.click("#next"); await pg.wait_for_timeout(500)
            await pg.screenshot(path=f"{out}-{i+1}.png", full_page=True)
        await b.close()
asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv)>3 else 0))
