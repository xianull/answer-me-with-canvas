# canvas

把一个流程、协议或者一段原理讲成「点一下走一步」的页面。agent 只写一份不带坐标的 content JSON（`canvas-content/v0`），布局、主题和动效都由 canvas 处理。

同一份 JSON 可以出成：

1. **交互页面**：逐步播放，当前这一步的边会画出来，板子里的表格、条形图、流程图一行一行亮起。
2. **导出文件**：Obsidian `.canvas`（JSON Canvas 1.0）、Excalidraw、mermaid、单文件 html。
3. **视频**：`--to video` 时用 3b1b 风格的深色画面，镜头跟着当前步走。

页面是只读的，不会写回源文件。

## 怎么填内容

照 `FILL-PROMPT.md` 写。里面最重要的是「怎么写字」一节：写完整的句子、先给真实例子、术语第一次出现就解释，不用符号代替语法，不中英对照说两遍。最短的骨架见 `minimal.content.json`。

## 三套主题

写在 JSON 的 `"theme"` 里，也可以在页面顶部的操作栏或 URL 的 `?theme=` 切换。视觉细节见 `DESIGN.md`。

| `theme` | 观感 | 适合 |
| --- | --- | --- |
| `book`（默认） | 暖白纸、衬线标题、墨黑加一点朱红 | 讲原理、讲概念 |
| `product` | 白底灰阶、圆角卡片、靛蓝强调 | 工程流程、协议、系统结构、数据 |
| `sketch` | 羊皮纸上的手绘草图：手写字、墨水色描边、线条略抖 | 入门、打比方、白板式讲解 |

## 一张图纸

默认是横屏平铺的「图纸」，密度向 Karpathy 转发过的那张 ASD-STE100 一页纸看齐：

- 外框带坐标刻度（1–8、A–D），底部是标题栏，内容来自 JSON 的 `meta`。
- 教学板排成 4 列：A | B B | C / D D | E | F。每块板左上是字母方块和标题，右上是一个等宽小标签（`sub`）。
- 表格紧凑，数字列右对齐；条形图画成带刻度和指针的量规。
- 鼠标移到一块板上，右上角会出现 ⤢，点开可以单独放大看这一块，按 Esc 关闭。
- 节点图（autoresearch、agent-message 这类分区图）也横向排成网格，跨分区的连线会绕开中间的节点。

## 顶部操作栏

所有页面的操作都在最顶上一条，滚动时也一直在：

- 左边：‹ 上一步、▶ 播放、下一步 ›、↺ 回到开头。
- 中间：「第几步 / 共几步」和这一步的说明；板内逐行走时，下面一行小字写着走到哪一行；消息类的步骤会在下面展开正文。底边一条细线是进度。
- 右边：视图（平铺 / 聚焦 / 逐页）和主题（书页 / 产品 / 手绘）。选择会写进地址栏，刷新后还在。

| 视图 | `display` | 形态 |
| --- | --- | --- |
| 平铺 | `sheet`（默认） | 所有板在一张图纸上，播放时逐行高亮 |
| 聚焦 | `deck` | 同一张图纸，只亮着当前这块 |
| 逐页 | `lesson` | 一次只看一块板，第 0 步是目录 |

样例：

- `?src=transformer.content.json`：Attention 怎么算，拿「猫坐在」三个字手算一遍（图纸 · book）
- `?src=claude-code-memory.content.json`：Agent 怎么记住「构建用 uv」（图纸 · sketch）
- `?src=autoresearch.content.json`：Karpathy 的 autoresearch 循环（分区图 · book）
- `?src=tcp-handshake.content.json`：TCP 三次握手（泳道 · product）
- `?src=agent-message.content.json`：修一个挂掉的测试，消息正文在操作栏下展开（分区图 · product）
- `?src=chart-demo.content.json`：加了依赖缓存以后构建快了多少（图表 · product）

## 运行

```
python3 server.py          # 或 tools/restart.sh
```

打开 http://127.0.0.1:8766/?src=transformer.content.json

- 往前走：点「下一步」、按 →、点图上淡虚线提示的下一条边，或者点当前节点。图纸里点某一行、某根量规，会直接跳到那一步；逐页视图的目录里点哪一步就跳到哪一步。
- 往回：‹ 或 ←。↺ 或 `r` 回到第 0 步。
- 自动播放：点「▶ 播放」或按空格，默认每 1.8 秒一步；地址加 `&autoplay=1` 打开就播；手动翻页会暂停。可在 JSON 里写 `playback`（见 SCHEMA.md）。
- 主题和视图：操作栏右边的按钮，或 `&theme=book|product|sketch`、`&display=sheet|deck|lesson`。手绘主题的字体从 jsDelivr 加载，离线时退回系统楷体。

## 文件

| 文件 | 作用 |
| --- | --- |
| `SCHEMA.md` / `canvas-content.schema.json` | content JSON 的定稿 schema |
| `FILL-PROMPT.md` | 填内容：选主题和形态、怎么写字、板子怎么拆 |
| `DESIGN.md` | 三套主题的设计变量、版式和动效原则 |
| `server.py` | 页面渲染和逐步播放器（只读），CSS 和 JS 都在 `PAGE` 模板里 |
| `layout.py` | 无坐标内容到坐标的布局，泳道渲染和各种导出都用它 |
| `export.py` | CLI 导出（见下） |
| `boards.py` | 教学板渲染：chips / callout / table / bars / flow / seq / code / list |
| `*.content.json` | 样例，见上文 |
| `chart.py` | `chart` 块的校验和 SVG 绘制（bar / line） |
| `verify.py` | 自检（会起一个无头浏览器） |
| `exports/` | 导出产物，运行导出命令后生成 |
| `shots/` | 截图 |

## 导出

底层图可以是 Mermaid 结构（节点/边）或 content JSON；分区 `groups`（字母 A/B/C…）导出时映射为 **Obsidian group 节点** / **Excalidraw frame** / **mermaid subgraph**。

```
python3 export.py SRC.content.json --to obsidian              # → exports/SRC.canvas（JSON Canvas 1.0）
python3 export.py SRC.content.json --to excalidraw [--theme sketch]
python3 export.py SRC.content.json --to html       [--theme book|product|sketch]
python3 export.py SRC.content.json --to mermaid
python3 export.py SRC.content.json --to video                 # 始终 3b1b；→ out/SRC.mp4
```

示例：

```
python3 export.py tcp-handshake.content.json --to obsidian
python3 export.py tcp-handshake.content.json --to excalidraw --theme sketch
python3 export.py transformer.content.json --to html --theme book
python3 export.py autoresearch.content.json --to mermaid
python3 export.py autoresearch.content.json --to video
```

视频：`/video?src=…` 是专用渲染页（3b1b 外观，`window.cvVideo.render(t)` 按时间确定性地画一帧），`export.py --to video` 用无头 Chromium 逐帧截图再交给 ffmpeg。样例产物：`out/tcp-handshake.mp4`（14.4 秒）、`out/autoresearch.mp4`（30.6 秒）。在浏览器里打开 `http://127.0.0.1:8766/video?src=autoresearch.content.json` 可以看渲染页（不会自己动，靠 `cvVideo.render(t)` 驱动）。

Excalidraw 导出：`--theme sketch` 保留 Excalidraw 自己的手绘线条和手写字，`book` / `product` 画直线、用 Nunito 字体。Obsidian `.canvas` 只按 JSON Canvas 1.0 做了结构校验，还没有在 Obsidian 里实际打开过；它不带主题，因为 JSON Canvas 没有背景字段。

## 校验

```
python3 verify.py     # 打印 PASS (N checks)；浏览器部分需要装 playwright，视频部分需要 ffmpeg
```
