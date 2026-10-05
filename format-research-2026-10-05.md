# 输入格式调研（写于 live-draft 时期；产品现名 canvas，见 SCHEMA.md）

**结论：推荐自研最小 content-only JSON（无坐标）作中间层；LLM 只填节点/边/文案/角色。导出时再布局并分别落到 mermaid、Obsidian `.canvas`、Excalidraw。不要让模型直接写完整 `.canvas` 或完整 `.excalidraw`。**

调研日期：2026-10-05（Asia/Shanghai）。现有代码：`/workspace/live-draft/server.py`（markdown + mermaid）。本报告不改代码。

示例文件：`/workspace/live-draft/content-only-autoresearch.example.json`（9 节点，含 KEEP / reset / Agent）。

---

## 1. Obsidian Canvas（JSON Canvas 1.0）

### 出处

- 官网：https://jsoncanvas.org/
- 规范：https://github.com/obsidianmd/jsoncanvas/blob/main/spec/1.0.md  
  镜像：https://jsoncanvas.org/spec/1.0/
- Obsidian 公告：https://obsidian.md/blog/json-canvas/
- 社区技能说明：https://github.com/kepano/obsidian-skills/blob/HEAD/skills/json-canvas/SKILL.md

### 顶层

`.canvas` 是纯 JSON。顶层两个可选数组：

- `nodes`
- `edges`

见规范 Top level 节。

### 节点（四种 type）

所有节点**必填**：`id`, `type`, `x`, `y`, `width`, `height`。可选：`color`。

| type | 额外必填 | 可选 |
| --- | --- | --- |
| `text` | `text`（Markdown 纯文本） | — |
| `file` | `file`（库内路径） | `subpath`（以 `#` 开头） |
| `link` | `url` | — |
| `group` | — | `label`, `background`, `backgroundStyle`（`cover` / `ratio` / `repeat`） |

节点数组顺序 = z-index（先画的在下）。出处：spec Nodes 节。

### 边

**必填**：`id`, `fromNode`, `toNode`。

可选：`fromSide` / `toSide`（`top|right|bottom|left`）、`fromEnd` / `toEnd`（`none|arrow`；默认 from=`none`、to=`arrow`）、`color`、`label`。

### 颜色

`canvasColor`：hex（如 `"#FF0000"`）或预设 `"1"`–`"6"`（红橙黄绿青紫）。预设具体色值由应用自定。出处：spec Color 节。

### LLM 若只写 id / label / edge：哪些必须有坐标？

**规范要求节点必须有 `x`,`y`,`width`,`height`。** 没有坐标的 `.canvas` 不符合 JSON Canvas 1.0，Obsidian 无法可靠打开。

可后填（由布局器生成，不让模型写）：

- 全部节点的 `x`, `y`, `width`, `height`
- 边的 `fromSide`, `toSide`（可按相对位置推）
- 节点/边 `color`（可由产品 `role` 映射）

模型应只产出内容：`id`, `type`（或 shape→type）、文案（`text`/`label`）、边的 `from`/`to`/`label`。导出器补坐标后再写 `.canvas`。

### 许可

JSON Canvas 规范与站点资源：**MIT**，可自由作导入/导出/存储格式。  
出处：https://jsoncanvas.org/ 、https://github.com/obsidianmd/jsoncanvas/blob/main/LICENSE

---

## 2. Excalidraw

### 出处

- 文件 JSON 文档：https://docs.excalidraw.com/docs/codebase/json-schema
- 源码类型：https://github.com/excalidraw/excalidraw/blob/master/packages/excalidraw/data/types.ts
- Element 类型：https://github.com/excalidraw/excalidraw/blob/master/packages/element/src/types.ts
- 简化 Skeleton API：https://docs.excalidraw.com/docs/@excalidraw/excalidraw/api/excalidraw-element-skeleton
- 场景字段说明（Excalidraw+）：https://plus.excalidraw.com/docs/api/scene-content-schema

### `.excalidraw` 主字段

| 字段 | 含义 |
| --- | --- |
| `type` | `"excalidraw"` |
| `version` | 数字（文档示例为 `2`） |
| `source` | 应用 URL，如 `"https://excalidraw.com"` |
| `elements` | 元素数组 |
| `appState` | 画布/偏好（如 `viewBackgroundColor`） |
| `files` | 图片等二进制映射（可空） |

### 常用元素：内容 vs 布局

**布局 / 几何（不要让 LLM 手写完整值）：**

- 共有：`x`, `y`, `width`, `height`, `angle`
- 箭头/线：`points`（相对坐标折线）
- 绑定：`startBinding`, `endBinding`（完整元素里）

**样式与协作元数据（也不该让 LLM 填完整文件）：**

- `seed`, `version`, `versionNonce`, `index`, `updated`, `isDeleted`, `groupIds`, `frameId`, `boundElements`, `locked`, `roughness`, `opacity`, `roundness`, …

**内容侧（适合中间层表达，再映射）：**

| 类型 | 内容相关 |
| --- | --- |
| `rectangle` / `diamond` / `ellipse` | 形状本身；可用 Skeleton 的 `label.text` 做成带字容器 |
| `text` | `text`（必填于 Skeleton）；另有 `fontSize`, `fontFamily`, `textAlign`, … |
| `arrow` / `line` | 连接语义；Skeleton 可用 `start`/`end` 的 `id` 绑定，`label.text` 作边标签 |

Skeleton API 仍要求形状有 `type` + `x` + `y`（text 还要 `text`）。完整元素字段更多。出处：Element Skeleton 文档。

**含义：** 即便用官方 Skeleton，也仍有坐标。正确做法是 content-only → 布局算 x/y → 再调 `convertToExcalidrawElements`，而不是让模型吐整份 `.excalidraw`。

### 许可

Excalidraw：**MIT**。  
出处：https://github.com/excalidraw/excalidraw/blob/master/LICENSE

---

## 3. Mermaid flowchart 对比与已知转换工具

### 现有用法

`/workspace/live-draft/server.py` 从 markdown 里取 ` ```mermaid ` 围栏；`/workspace/live-draft/autoresearch.md` 是 Autoresearch Loop 样例。`/workspace/live-draft/README.md` 写明：模型只写节点和边，不写坐标、不写动画。

### 谁最容易让 LLM 写？

| 格式 | LLM 难度 | 原因 |
| --- | --- | --- |
| **Mermaid flowchart** | 最低 | 短文本；`A[label]` / `B{decision}` / `A -->|yes| B`；训练语料多；无坐标 |
| Content-only JSON（本报告建议） | 低 | 结构化、易校验；仍无坐标；比 mermaid 更稳地映射 role/导出 |
| Obsidian `.canvas` | 高 | **强制** x/y/width/height；模型乱填坐标会叠在一起 |
| 完整 Excalidraw JSON | 最高 | 字段极多（seed/versionNonce/points/…）；易坏、难 diff |

**结论：** 对人写/模型写，mermaid 最省事；对多后端导出与校验，content-only JSON 更合适。产品可继续接受 mermaid 作一种输入，解析进同一中间层。

### 已知工具 / 库（名字 + 链接）

| 方向 | 名字 | 链接 |
| --- | --- | --- |
| Mermaid → Excalidraw | `@excalidraw/mermaid-to-excalidraw`（官方，MIT） | https://www.npmjs.com/package/@excalidraw/mermaid-to-excalidraw ；文档 https://docs.excalidraw.com/docs/@excalidraw/mermaid-to-excalidraw/api |
| Mermaid → Excalidraw（CLI/MCP） | `excalimaid` | https://www.npmjs.com/package/excalimaid |
| Obisidian Canvas → Mermaid / Excalidraw 等 | Canvas Export 插件（`rmoff/obsidian-canvas-export`） | https://github.com/rmoff/obsidian-canvas-export ；https://community.obsidian.md/plugins/canvas-export |
| Canvas → Mermaid | Canvas2Mermaid | https://github.com/haclkmans/canvas2mermaid |
| Mermaid 在笔记里增强预览 | Mermaid Canvas 插件 | https://community.obsidian.md/plugins/mermaid-canvas |

注意：社区里 **Canvas→Mermaid** 多于 **Mermaid→Canvas**。论坛需求「把 mermaid 拆成多个 Canvas 卡片」仍多为想法，无官方一键（https://forum.obsidian.md/t/integrate-mermaid-plantuml-functionality-into-canvas/59834）。自研 content-only → `.canvas` 更干净。

布局可后接 **dagre**（`@dagrejs/dagre`）：给无坐标图算 x/y。https://github.com/dagrejs/dagre

---

## 4. 中间层建议：content-only JSON

### 原则

- LLM / 作者只写：**id、文案、形状、边、可选 role/样式意图**
- **禁止**写入：`x`,`y`,`width`,`height`,`points`,`seed`,`versionNonce` 等
- 管线：`content-only` →（可选）校验 → **布局**（dagre 等）→ 导出
  - mermaid 字符串（现有 live-draft）
  - JSON Canvas `.canvas`（补齐必填几何）
  - Excalidraw（Skeleton 或经 `convertToExcalidrawElements`；几何由布局填）

### 最小字段（产品 schema，非官方 Obsidian/Excalidraw 字段）

```text
format, title?, direction?
nodes[]: id, label, shape? (rect|diamond|ellipse|rounded), role?
edges[]: from, to, label?, style? (solid|dashed)
```

`role`（如 `agent` / `keep` / `reset`）仅产品元数据，用于配色与播放；**不是** JSON Canvas 或 Excalidraw 规范字段。导出时映射到对方允许的 `color` / `strokeColor` / `classDef`。

### 最小示例（Autoresearch：KEEP / reset / Agent）

完整文件：`/workspace/live-draft/content-only-autoresearch.example.json`（9 节点）。

摘要：

```json
{
  "format": "live-draft-content/v0",
  "title": "Karpathy's Autoresearch Loop",
  "direction": "TB",
  "nodes": [
    { "id": "agent", "label": "Agent\nClaude, Codex...", "shape": "rect", "role": "agent" },
    { "id": "step1", "label": "1 Try one idea\nedit train.py", "shape": "rect", "role": "step" },
    { "id": "decide", "label": "lower val_bpb?", "shape": "diamond", "role": "decision" },
    { "id": "keep", "label": "KEEP\nadvance the branch", "shape": "rect", "role": "keep" },
    { "id": "reset", "label": "git reset\nback to last good commit", "shape": "rect", "role": "reset" },
    { "id": "log", "label": "Log result\nresults.tsv", "shape": "rect", "role": "step" }
  ],
  "edges": [
    { "from": "agent", "to": "step1" },
    { "from": "decide", "to": "keep", "label": "yes" },
    { "from": "decide", "to": "reset", "label": "no" },
    { "from": "log", "to": "step1", "label": "repeat forever" }
  ]
}
```

（示例文件含 step1–step4 共 9 节点；上表为节选。）对照现有 mermaid：`/workspace/live-draft/autoresearch.md`。

### 导出时如何对接官方字段（不发明假字段）

| 中间层 | → Mermaid | → JSON Canvas | → Excalidraw |
| --- | --- | --- | --- |
| `id` + `label` + `shape:rect` | `id["label"]` | `type:"text"`, `text` | `type:"rectangle"` + `label.text`（Skeleton） |
| `shape:diamond` | `id{"label"}` | 仍用 `text` 节点（Canvas **无** diamond 类型） | `type:"diamond"` + `label.text` |
| `edges.from/to/label` | `A -->|label| B` | `fromNode`/`toNode`/`label` + 生成的 `id` | `type:"arrow"` + `start.id`/`end.id` |
| `x,y,w,h` | 不需要 | **必填，布局器写** | Skeleton 的 `x,y`（及可选宽高），布局器写 |

Canvas 没有 flowchart 菱形：决策节点在 `.canvas` 里用 `text` 卡片 + 文案/颜色表达即可（规范仅 `text|file|link|group`）。

---

## 5. 许可与依赖

| 项 | 许可 / 说明 | 出处 |
| --- | --- | --- |
| JSON Canvas 格式 | MIT；可自由实现读写 | https://jsoncanvas.org/ ；https://github.com/obsidianmd/jsoncanvas/blob/main/LICENSE |
| Excalidraw | MIT | https://github.com/excalidraw/excalidraw/blob/master/LICENSE |
| `@excalidraw/mermaid-to-excalidraw` | MIT（npm 页标注） | https://www.npmjs.com/package/@excalidraw/mermaid-to-excalidraw |
| `@excalidraw/excalidraw`（含 `convertToExcalidrawElements`） | MIT | 同上仓库 LICENSE |
| PyJSONCanvas（Python 序列化 `.canvas`） | MIT；`pip install PyJSONCanvas` | https://pypi.org/project/PyJSONCanvas/ ；https://github.com/CheeksTheGeek/PyJSONCanvas |
| `@trbn/jsoncanvas`（TS 数据结构） | npm 包，规范实现 | https://www.npmjs.com/package/@trbn/jsoncanvas |
| dagre 布局 | 常用图布局（给中间层补坐标） | https://github.com/dagrejs/dagre |

**没有**「只序列化、零布局」且同时覆盖三种导出的单一官方包。务实组合：

- 自研 content-only（几十行校验即可）
- Python：`PyJSONCanvas` 写 `.canvas`
- JS：`@excalidraw/mermaid-to-excalidraw` 和/或 Skeleton + `convertToExcalidrawElements`
- 布局：`dagre`（或自写网格）

现有 live-draft 仍可只读 mermaid；中间层是后续适配 Obsidian / Excalidraw 的前提。

---

## 6. 不做

1. **不让 LLM 手写坐标**（`x`,`y`,`width`,`height`,`points`）。
2. **不让模型输出完整 `.excalidraw` 大 JSON**（含 `seed` / `versionNonce` / `files` dataURL 等）。
3. **不把不完整的无坐标对象冒充合法 `.canvas`**（规范要求几何必填）。
4. **不发明** Obsidian/Excalidraw 官方不存在的字段名塞进导出文件（`role` 只留在中间层或映射掉）。
5. **本调研不改** `/workspace/live-draft/server.py` 与现有 markdown 管线。

---

## 参考路径速查

| 资源 | 路径 / URL |
| --- | --- |
| 本报告 | `/workspace/live-draft/format-research-2026-10-05.md` |
| 中间 JSON 示例 | `/workspace/live-draft/content-only-autoresearch.example.json` |
| 现有 mermaid 样例 | `/workspace/live-draft/autoresearch.md` |
| 现有服务 | `/workspace/live-draft/server.py` |
| JSON Canvas spec | https://github.com/obsidianmd/jsoncanvas/blob/main/spec/1.0.md |
| Excalidraw JSON | https://docs.excalidraw.com/docs/codebase/json-schema |
| Excalidraw Skeleton | https://docs.excalidraw.com/docs/@excalidraw/excalidraw/api/excalidraw-element-skeleton |
