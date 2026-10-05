# canvas-content/v0

一份 JSON 只写**教学内容**。布局、主题、动画、坐标由 canvas 处理。

机器可读版：`canvas-content.schema.json`。

## 三条硬规则

1. **内容拆解**：只用苏格拉底 / 第一性原理 / 总-分-总。`groups` = 教学板块（A/B/C…）；默认 **`orientation: landscape`** 横板并排，不是泳道。可读性优先（短句、每板≤5 要点）。HTML **不渲染**底部 `rules` 总结表。
2. **三套主题**：`book`（默认）/ `product` / `sketch`，写在 `theme` 里，按内容挑一套（见 FILL-PROMPT.md）。**`3b1b` 只用于视频导出。**
3. **省 token**：模型尽量只填 `title` + `groups` + `nodes` + `steps({from,to,caption})`。`edges` / playback / role / 坐标都可以省，渲染器会补默认值。

## 最小填空（推荐）

```json
{
  "format": "canvas-content/v0",
  "title": "修一个挂掉的测试",
  "groups": [
    {"id": "ask", "title": "提问", "members": ["u1"]},
    {"id": "clarify", "title": "澄清", "members": ["p1"]},
    {"id": "deepen", "title": "深挖", "members": ["c1", "t1"]},
    {"id": "synth", "title": "综合", "members": ["a1"]}
  ],
  "nodes": [
    {"id": "u1", "label": "用户提问\nCI 挂了"},
    {"id": "p1", "label": "拆任务"},
    {"id": "c1", "label": "改代码"},
    {"id": "t1", "label": "跑测试"},
    {"id": "a1", "label": "答复"}
  ],
  "steps": [
    {"from": "u1", "to": "p1", "caption": "提出问题"},
    {"from": "p1", "to": "c1", "caption": "澄清成任务"},
    {"from": "c1", "to": "t1", "caption": "深挖"},
    {"from": "t1", "to": "a1", "caption": "综合答复"}
  ]
}
```

短键：`t/g/n/s/m/l/c/f/i`（见 FILL-PROMPT.md）。`steps` 也可用 `"a->b|说明"` 字符串。

## 两种模式

同一份 content JSON，两种出法：

| 模式 | 用途 | 外观 | 怎么出 |
| --- | --- | --- | --- |
| 1. 展示页（交互 HTML） | 在浏览器里讲解：点击逐步、自动播放、动效（边逐条画出、节点平滑点亮） | 主题 `book` / `product` / `sketch`；分区带字母徽章 A、B、C… | `server.py` 实时出，或 `export.py --to html --theme …` |
| 2. 3b1b 视频 | 只在导出视频时用 | 深底、大字、节点只描边、镜头跟随当前步、边画出、字幕 | `export.py <src> --to video` → `out/<name>.mp4`（1920×1080，30fps，H.264） |

视频是确定性渲染的：`/video?src=…` 是专用渲染页，暴露 `window.cvVideo.render(t)`，按时间 t 直接算出这一帧（没有计时器和 CSS 动画）；无头 Chromium 逐帧截图，ffmpeg 编码。时间线：开场全景 1.4 秒，每步 `interval_ms/1000 + 0.9` 秒（默认 2.7 秒），结尾回到全景 2.2 秒。只含 `chart`、没有节点的文件不能出视频（会报错）。

## 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `format` | 是 | `canvas-content/v0`。旧的 `live-draft-content/v0` 仍能读，读进来当 v0 处理。 |
| `title` | 否 | 页面大标题。 |
| `notes` | 否 | 图下的一段说明。 |
| `direction` | 否 | `TB`（默认）/ `LR` 等。只在 mermaid 布局下起作用。 |
| `theme` | 否 | 页面：`book`（默认，书页）/ `product`（产品）/ `sketch`（手绘白板）。写了别的值会退回 `book`。`3b1b` 只用于视频。 |
| `display` | 否 | 教学板的形态：`sheet`（默认，横屏图纸）/ `deck` / `lesson`。读者可以在顶部操作栏切换，URL 的 `?display=` 也能覆盖。一般不用写。 |
| `cols` | 否 | 图纸的列数，推荐 `4`，配合第 2、4 块板的 `span: 2` 排成 A \| B B \| C / D D \| E \| F。 |
| `meta` | 否 | 图纸底部标题栏，`{"名称": "值"}`，3 到 5 项，值要短。 |
| `section_cols` | 否 | 分区图横排几列。不写时 3–4 个分区排 2 列，5 个以上排 3 列；`orientation: portrait` 时竖叠。 |
| `layout` | 否 | `lanes` 或 `mermaid`。不写时：用到泳道就走 `lanes`，否则走 `mermaid`。 |
| `orientation` | 否 | 教学板排布：`landscape`（默认，横板并排）/ `portrait`（竖叠）。 |
| `groups[]` | 否 | 分区，见下文。只写归属，不写坐标。 |
| `lanes[]` | 否 | 泳道，`{id, label, subtitle?, role?}`，从左到右排。没有 `groups` 时，泳道就是分区（占满高度、带标题和底色）。 |
| `nodes[]` | 是 | `{id, label, shape?, role?, lane?}`。`label` 第一行是标题，`\n` 之后是小字。**不许写** `x` `y` `width` `height` `points`，写了直接报错。 |
| `edges[]` | 是 | `{from, to, label?, style?}`。`style` 可以是 `solid` / `dashed` / `thick` / `invisible`。`invisible` 只用来提示布局，不画出来，也不算一步。 |
| `start` | 否 | 第 0 步就点亮的节点，可以是一个 id 或一组。不写时用第一步的起点。 |
| `steps[]` | 否 | 播放顺序。每项可以是边的下标、`"a->b"`，或 `{edge \| edges, focus?, caption?, detail?, optional?, reveal?, highlight?}`。不写时每条可见边算一步，按 `edges` 的顺序。只控制图表的步骤可以不写 `edge`。 |
| `chart` | 否 | 最小数据图，见下文。有 `chart` 时 `nodes` / `edges` 可以为空或不写。 |
| `playback` | 否 | 自动播放设置 `{autoplay?, interval_ms?, loop?}`，见下文。填图的模型不用写。 |

`role` 的取值：`plain` `step` `decision` `agent` `keep` `reset` `human` `editable` `locked` `dashed` `caption`。它只是产品元数据，由各个后端映射成颜色（mermaid classDef / JSON Canvas 预设色 / Excalidraw 填充色）。

## `groups` 分区

```jsonc
"groups": [
  { "id": "question",  "title": "要改进的目标", "subtitle": "val_bpb 越低越好",
    "members": ["goal", "agent"], "role": "agent" },
  { "id": "premises",  "title": "谁能动哪个文件", "subtitle": "人写规则，Agent 只改一个",
    "members": ["human", "editable", "locked"], "role": "editable" },
  { "id": "mechanism", "title": "一轮实验怎么跑", "subtitle": "改、提交、训练、读分",
    "members": ["s1", "s2", "s3", "s4"], "role": "step" },
  { "id": "conclude",  "title": "留下还是退回", "subtitle": "每一轮都记一笔",
    "members": ["decide", "keep", "reset", "log", "crash"], "role": "decision" }
]
```

- `id` / `title` 必填；`subtitle`（教学板里也可以写短键 `sub`，显示在板的右上角）、`role`（同节点 role，决定字母徽章的颜色）、`span`（教学板跨几列）可选。
- 字母：分区按声明顺序自动编为 A、B、C、D、E…（超过 Z 接 AA）。模型不用写；想改时写 `label`（groups）或 `badge`（groups 和 lanes 都行，因为 lanes 的 `label` 是标题），最多 3 个字符。页面区域（`@chart` 等）接在图分区后面继续编号。
- `members` 是节点 id 列表。一个节点只能属于一个分区；没被任何分区收进去的节点放在最右的无标题区。
- **`groups` 是教学板块**（lettered panels）：默认 **landscape** 横板并排（宽屏教学幻灯），`portrait` 时才竖叠。每块一节讲解，不是 Client/Server 泳道。标题写这一块具体讲什么，不要套「问题 / 前提 / 机制」这类模板名。板内节点按局部依赖从左往右排。**不要**依赖页面底部 `rules` 表（已不展示）。
- **`lanes` 才是并排泳道**，只给真正的时序双方（Client/Server）。讲解型内容不要用 lanes。
- 只写归属，坐标由 `layout.py` 算。
- 页面区域：`members` 写 `@chart`、`@caption`、`@notes` 时，图表、说明框、注释会被包进带标题的页面分区（见 `chart-demo.content.json`）。
- 有 `groups` 时忽略 `node.lane`。
- 展示页渲染：圆角区域 + 左上「字母徽章 + 标题」+ 可选小字副标题。book 是浅纸色底，product 是白卡片，sketch 只画彩色虚线框。连线穿过分区时会从标题底下走，不会压住标题。
- 视频渲染：极暗底 + 细描边 + 角色色标题；镜头聚焦时右上角固定显示当前分区的「字母 + 标题」，所以放大后也知道在哪个区。
- mermaid：每个分区是一个 `subgraph`，标题是「A · 标题」，移到左上。Obsidian：每个分区一个 `type: "group"` 节点，标签「A 标题 · 副标题」。Excalidraw：每个分区一个 `frame`，名字同上，节点带 `frameId`。

## 自动播放 `playback`

```jsonc
"playback": { "autoplay": false, "interval_ms": 1800, "loop": false }   // 都是默认值
```

- 页面上有「▶ 播放 / ❚❚ 暂停」按钮，空格键切换；默认每 1.8 秒走一步，`interval_ms` 会被夹在 300..60000。
- `autoplay: true` 或 URL `?autoplay=1` 打开页面就开始播放；`?autoplay=0` 可以压掉内容里的 `autoplay`。
- 手动点「上一步 / 下一步 / 重来」或按方向键会暂停自动播放。走到最后一步就停；`loop: true` 时回到第 0 步重新播。
- 视频导出也用 `interval_ms` 决定每步时长（加 0.9 秒留给镜头和画线）。
- 填图的模型不需要写这个字段。

## 步骤里的 `detail`

`detail` 用来放一步里比较长的原文，比如消息正文、工具调用参数、返回结果。它显示在说明文字下面，用等宽字体，有左边线。边标签只写短的消息类型，例如 `① request`、`③ tool_call`。样例：`agent-message.content.json`，有 User、Planner、Coder、Tool 四条泳道，共 8 条消息。


## `groups[].blocks` 教学板内容（密板，非稀疏节点图）

每个分区可以是一块 **lettered teaching board**（A–J），内嵌内容块，而不是只挂节点：

```json
{"id": "a", "title": "一句话结论", "blocks": [
  {"type": "callout", "text": "…"},
  {"type": "table", "headers": ["层","载体"], "rows": [["热","`USER.md`"]]},
  {"type": "bars", "items": [{"label": "MEMORY.md", "value": 10199, "max": 10240, "unit": "B"}]},
  {"type": "flowchart", "mermaid": "flowchart LR\n  A-->B"},
  {"type": "sequence", "mermaid": "sequenceDiagram\n  A->>B: hi"},
  {"type": "code", "lang": "py", "text": "def f():\n  pass"},
  {"type": "mermaid", "mermaid": "flowchart TD\n  X-->Y"},
  {"type": "list", "items": ["要点1", "要点2"]}
]}
```

短键：`b`=`blocks`，块内 `k`=`type`，`t`=`text`，`h`/`r`=`headers`/`rows`，`i`=`items`，`m`=`mermaid`，`lg`=`lang`。  
`type` 别名：`co` callout、`tb` table、`flow` flowchart、`seq` sequence、`md` mermaid。  
有 `blocks` 时 `nodes` 可省略；`steps` 用 `{"g":"a","c":"说明"}` 按板逐步展开（自动播放 A→J）。

动效：分区聚焦、表格行高亮、预算针动画、流程图/时序步进描边、点击某板跳到该步。

## `chart`

```jsonc
"chart": {
  "type": "bar",                // bar | line
  "title": "CI 构建时间（分钟）",
  "unit": "min", "x_label": "周", "y_label": "分钟",
  "categories": ["W1", "W2", "W3"],
  "series": [ { "name": "改缓存前", "values": [14, 15, 16] },
              { "name": "改缓存后", "values": [14, 15, 9] } ],
  "sample": true,               // 编的数字必须写 true，页面会显示「示例数据」角标
  "source": "示例数据，为演示虚构"
}
```

- 只写标签和数值。比例尺、刻度、坐标都由 canvas 计算。图表里写了 `x` `y` `width` `height` `points` `scale` `ticks` 会直接报错。
- 每个系列的 `values` 长度必须和 `categories` 一样。没有数据的位置写 `null`。
- 和步骤同步：某一步写 `reveal: n`，走完这一步就显示前 n 组。只要有任何一步写了 `reveal`，第 0 步就一组都不显示；都没写的话，一开始就全部显示。某一步写 `highlight: "W3"`（也可以写下标），这一步会高亮那一组。
- 交互：鼠标悬停时临时高亮，并在图下显示各系列的数值；点一下会钉住高亮，再点同一组取消。「重来」也会清掉钉住的高亮。还没显示出来的组不响应。
- 三个主题各有一套系列颜色（`--chart-1..3`）和高亮色（`--chart-hl`）。
- 样例：`chart-demo.content.json`（柱状图，示例数据，默认是格子本主题）。

## 播放语义

- 第 k 步做完后，第 1..k 步的边是实线，第 k 步的边会走一遍流动动画，并有一个小圆点沿边跑过去。`focus` 节点是粗框。
- 走过的节点会点亮，还没走到的变淡。不在任何一步里的节点一直正常显示，当作背景信息。
- 下一步要走的边是虚线淡显，点它、点粗框节点、点「下一步」或按 → 键都能前进。← 键是上一步，`r` 键是重来，空格是自动播放 / 暂停。
- 每条参与步骤的边，标签前有一个圆形步骤编号（第几步）；标签里手写的 ①..⑳ 会被去掉，避免重复。
- 页面只读，不写回源文件，也没有 POST 接口。

## 视频外观（3b1b 风格，只用于 `--to video`）

- 深色底（#1c1c1c）。节点只描边、不填充，描边颜色按 `role` 区分：默认蓝，`keep` 绿，`reset` 和 `human` 红，`agent` 黄，`editable` 青绿，`decision` 金，`dashed` 灰。边是浅色细线，当前这一步是黄色。
- 镜头：每一步开头约 0.9 秒内，viewBox 缓动到当前步的节点和边上（按 16:9 取景），其他分区变暗；开场和结尾是全景。
- 当前边从起点画到终点（stroke-dashoffset），箭头最后出现，一个黄点沿边跑过去；第一次出现的节点描边画出，文字随后淡入。
- 当前步的说明以大字字幕显示在底部，`detail` 以小号等宽字显示在字幕下。
- 只用了配色和排版来模仿，没有拷贝 3b1b 的素材、logo 或字体文件。配色值来自开源 manim 的颜色常量风格。
- 展示页不再提供 3b1b 主题，也没有「全景」按钮；Excalidraw / Obsidian 导出也不接受 `--theme 3b1b`。

## 后端

| 后端 | 怎么出 | 坐标从哪来 |
| --- | --- | --- |
| canvas 页面（HTML） | `server.py` 实时出，或 `export.py --to html` 生成单文件 | `lanes`：`layout.py`；`mermaid`：浏览器里的 mermaid |
| Obsidian `.canvas` | `export.py --to obsidian` | `layout.py` |
| Excalidraw | `export.py --to excalidraw` | `layout.py` |
| mermaid | `export.py --to mermaid` | 不需要 |
| 视频 mp4（3b1b 风格） | `export.py --to video [--fps 30] [-o out/x.mp4]`，需要 playwright + ffmpeg | `layout.py`（同展示页） |

样例：`autoresearch.content.json`（4 个分区：文件 / 执行者 / 循环 / 异常，10 步）、`tcp-handshake.content.json`（2 条泳道、4 步）、`agent-message.content.json`（4 条泳道、8 条消息）、`chart-demo.content.json`（柱状图）。

导出时，图表会写成一个文本节点：Obsidian 里是一张 markdown 表，Excalidraw 里是一块等宽文本。导出的是数据，不是图。
