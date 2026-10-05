---
name: canvas
description: 把一个原理、流程或协议讲成一张能逐步播放的横屏图纸（HTML）时使用。只写一份 canvas-content/v0 内容 JSON，布局、主题、动效都由渲染器出；可以导出单文件 HTML、Obsidian .canvas、Excalidraw 或 3b1b 风格视频。
---

# canvas

省 token 的做法：只读一份说明，只写内容，一条命令出 HTML。

1. 读 `FILL-PROMPT.md`（约 2k token）。它有主题、版式、写作规则、短键和骨架，够用了。不要读 `server.py`；`SCHEMA.md` 只在要用泳道（`lanes`）、图表（`chart`）或 `playback` 时再查。
2. 写一份 `<名字>.content.json`：用短键，不缩进，不写坐标和样式。一张六块板的图纸一般 1.5k 左右 token。
3. 出 HTML：`python3 export.py <名字>.content.json --to html -o <名字>.html`。命令正常退出就说明内容合法；出错时会直接说哪一项不对。
   - 换主题：加 `--theme book|product|sketch`，或在 JSON 里写 `"theme"`。
   - 边写边看：`python3 server.py`，打开 `http://127.0.0.1:8766/?src=<名字>.content.json`。逐页视图（`&display=lesson`）只在这种方式下可用。
4. 其他导出：`--to obsidian|excalidraw|mermaid`；节点图（不是教学板）还能 `--to video` 出 3b1b 风格的 mp4，需要 ffmpeg 和 playwright。

`verify.py` 是改渲染器时用的完整自检，要起浏览器跑几分钟，生成内容时不用跑。

样例：`transformer.content.json`（图纸）、`autoresearch.content.json`（分区节点图）、`minimal.content.json`（最短骨架）。
