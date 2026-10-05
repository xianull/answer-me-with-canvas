#!/usr/bin/env python3
"""canvas: content-only JSON or markdown+mermaid, rendered as a click-to-step animated diagram.

Read-only. The page never writes to the source file and there is no writeback API.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import chart as charts
import boards
import layout as lay

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8766

CONTENT_FORMAT = "canvas-content/v0"
THEMES = ("book", "product", "sketch")  # page; 3b1b = video only
VIDEO_THEME = "3b1b"  # used only by the video renderer (/video, export.py --to video)
LEGACY_FORMATS = {"live-draft-content/v0"}  # accepted once, read as canvas-content/v0
ROLE_CLASSDEFS = {
    "agent": "fill:#111111,stroke:#111111,color:#FFFFFF",
    "keep": "fill:#DCFCE7,stroke:#16A34A,color:#166534",
    "reset": "fill:#FEE2E2,stroke:#DC2626,color:#991B1B",
    "human": "fill:#FEE2E2,stroke:#DC2626,color:#991B1B",
    "editable": "fill:#F3F4F6,stroke:#9CA3AF,color:#374151",
    "locked": "fill:#FFFFFF,stroke:#111111,color:#111111",
    "dashed": "fill:#FFFFFF,stroke:#111111,color:#111111,stroke-dasharray:5 4",
    "caption": "fill:transparent,stroke:transparent,color:#333333",
    "step": "fill:#FFFFFF,stroke:#111111,color:#111111",
    "decision": "fill:#FFFFFF,stroke:#111111,color:#111111",
    "plain": "fill:#FFFFFF,stroke:#111111,color:#111111",
}

MARK_RE = re.compile(r"<!--\s*section:([A-Za-z0-9_-]+)\s*-->")
FENCE_RE = re.compile(r"```mermaid[^\n]*\n.*?```", re.DOTALL)
NODE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(.*?)\]|\(\((.*?)\)\)|\((.*?)\)|\{(.*?)\})?$")
# a --> b, a -.-> b, a ==> b, a ~~~ b, each with an optional |label|
EDGE_RE = re.compile(r"^(.*?)\s+(-->|-\.->|==>|~~~)\s*(?:\|([^|]*)\|\s*)?(.*?)$")
NODE_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def resolve_src(src: str | None) -> Path:
    """Only files inside this directory. Relative paths stay relative to it."""
    root = ROOT.resolve()
    raw = (src or "tcp-handshake.content.json").strip() or "tcp-handshake.content.json"
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if path != root and root not in path.parents:
        raise ValueError("src must stay inside canvas")
    if not path.is_file():
        raise ValueError("src is not a file")
    return path


def locate_fence(text: str, section_id: str | None = None):
    """Return (start, end, section_id) of the mermaid fence after a section marker."""
    marks = list(MARK_RE.finditer(text))
    mark = None
    if section_id:
        mark = next((m for m in marks if m.group(1) == section_id), None)
    if mark is None and marks:
        mark = marks[0]
    begin = mark.end() if mark else 0
    fence = FENCE_RE.search(text, begin)
    if fence is None:
        raise ValueError("mermaid fence not found")
    return fence.start(), fence.end(), (mark.group(1) if mark else "flow")


def is_content_json(path: Path) -> bool:
    return path.suffix.lower() == ".json"


def mermaid_escape_label(label: str) -> str:
    text = str(label or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace('"', "#quot;")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("\n", "<br/>")


def node_shape_token(node_id: str, label: str, shape: str) -> str:
    lab = mermaid_escape_label(label)
    shape = (shape or "rect").lower()
    if shape == "diamond":
        return f'{node_id}{{"{lab}"}}'
    if shape in {"ellipse", "circle"}:
        return f'{node_id}(("{lab}"))'
    if shape == "rounded":
        return f'{node_id}("{lab}")'
    return f'{node_id}["{lab}"]'


def edge_keys(edges: list) -> list:
    """Mermaid ids edges as L_<from>_<to>_<n>, n counting repeats of the same pair."""
    seen = {}
    keys = []
    for left, right in edges:
        n = seen.get((left, right), 0)
        seen[(left, right)] = n + 1
        keys.append(f"L_{left}_{right}_{n}")
    return keys


def content_to_mermaid(data: dict) -> str:
    """Content JSON -> mermaid flowchart. Regions (groups or lanes) become subgraphs."""
    direction = str(data.get("direction") or "TB").upper()
    if direction not in {"TB", "TD", "LR", "RL", "BT"}:
        direction = "TB"
    lines = [f"flowchart {direction}"]
    roles_used = {}
    tokens = {}
    order = []
    for node in data.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        if not NODE_ID_RE.match(node_id):
            continue
        label = node.get("label")
        if label is None:
            label = node_id
        role = str(node.get("role") or "plain").strip() or "plain"
        if role not in ROLE_CLASSDEFS:
            role = "plain"
        tokens[node_id] = node_shape_token(node_id, label, str(node.get("shape") or "rect"))
        order.append(node_id)
        roles_used.setdefault(role, []).append(node_id)
    placed = set()
    for g in lay.resolve_groups(data):
        if g["id"] == "_loose" or not NODE_ID_RE.match(g["id"]):
            continue
        title = (f"{g['letter']} · " if g.get("letter") else "") + g["title"] + (f" · {g['subtitle']}" if g["subtitle"] else "")
        lines.append(f'  subgraph grp_{g["id"]}["{mermaid_escape_label(title)}"]')
        lines.append("    direction TB")
        for m in g["members"]:
            if m in tokens:
                lines.append(f"    {tokens[m]}")
                placed.add(m)
        lines.append("  end")
    for node_id in order:
        if node_id not in placed:
            lines.append(f"  {tokens[node_id]}")
    for edge in data.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        left = str(edge.get("from") or "").strip()
        right = str(edge.get("to") or "").strip()
        if not left or not right:
            continue
        style = str(edge.get("style") or "solid").lower()
        arrow = {"dashed": "-.->", "thick": "==>", "invisible": "~~~"}.get(style, "-->")
        label = edge.get("label")
        if label and style != "invisible":
            lines.append(f"  {left} {arrow}|{mermaid_escape_label(label)}| {right}")
        else:
            lines.append(f"  {left} {arrow} {right}")
    for role, ids in roles_used.items():
        lines.append(f"  classDef {role} {ROLE_CLASSDEFS[role]}")
        lines.append(f"  class {','.join(ids)} {role}")
    return "\n".join(lines)



def expand_content(data: dict) -> dict:
    """Normalize a minimal fill into the full v0 shape the renderer expects.

    Agents may send only title + groups + nodes + steps({from,to,caption}).
    Short keys: t/title, g/groups, n/nodes, s/steps, m/members, l/label, c/caption,
    f|from, to, i|id. Edges are inferred from steps when omitted.
    """
    if not isinstance(data, dict):
        raise ValueError("content json must be an object")
    # short top-level keys
    alias = {"t": "title", "g": "groups", "n": "nodes", "s": "steps", "e": "edges",
             "d": "direction", "th": "theme", "nt": "notes", "st": "subtitle"}
    for short, long in alias.items():
        if short in data and long not in data:
            data[long] = data.pop(short)
        elif short in data:
            data.pop(short, None)

    # groups: allow [{title, members}] without id; map m→members
    groups = data.get("groups")
    if isinstance(groups, list):
        out_g = []
        for i, g in enumerate(groups):
            if not isinstance(g, dict):
                continue
            if "m" in g and "members" not in g:
                g["members"] = g.pop("m")
            if "l" in g and "title" not in g:
                g["title"] = g.pop("l")
            if "i" in g and "id" not in g:
                g["id"] = g.pop("i")
            if not g.get("id"):
                base = "".join(c if c.isalnum() else "_" for c in str(g.get("title") or f"g{i}"))[:24] or f"g{i}"
                g["id"] = base.lower().strip("_") or f"g{i}"
            if not g.get("title"):
                g["title"] = g["id"]
            g.setdefault("members", [])
            boards.expand_group_blocks(g)
            out_g.append(g)
        data["groups"] = out_g

    # nodes: allow string "id|label" or {id,label}; map i/l
    nodes = data.get("nodes") or []
    if not isinstance(nodes, list):
        raise ValueError("nodes must be an array")
    out_n = []
    for i, n in enumerate(nodes):
        if isinstance(n, str):
            if "|" in n:
                nid, lab = n.split("|", 1)
            elif ":" in n:
                nid, lab = n.split(":", 1)
            else:
                nid, lab = f"n{i}", n
            n = {"id": nid.strip(), "label": lab.strip()}
        elif isinstance(n, dict):
            if "i" in n and "id" not in n:
                n["id"] = n.pop("i")
            if "l" in n and "label" not in n:
                n["label"] = n.pop("l")
            if not n.get("id"):
                n["id"] = f"n{i}"
            if "label" not in n:
                n["label"] = n["id"]
        else:
            continue
        out_n.append(n)
    data["nodes"] = out_n

    # steps: {from,to,caption} or {f,to,c} → ensure edges exist; map to edge form
    steps = data.get("steps")
    edges = list(data["edges"]) if isinstance(data.get("edges"), list) else []
    edge_pairs = {(str(e.get("from")), str(e.get("to"))) for e in edges if isinstance(e, dict) and e.get("from") and e.get("to")}

    def add_edge(frm, to, label=""):
        key = (str(frm), str(to))
        if key in edge_pairs:
            return
        edges.append({"from": key[0], "to": key[1], **({"label": label} if label else {})})
        edge_pairs.add(key)

    if isinstance(steps, list):
        out_s = []
        for item in steps:
            if isinstance(item, str) and "->" in item:
                # "a->b|caption" or "a->b"
                left, rest = item.split("->", 1)
                if "|" in rest:
                    right, cap = rest.split("|", 1)
                else:
                    right, cap = rest, ""
                add_edge(left.strip(), right.strip())
                out_s.append({"edge": f"{left.strip()}->{right.strip()}", **({"caption": cap.strip()} if cap.strip() else {})})
                continue
            if not isinstance(item, dict):
                out_s.append(item)
                continue
            if "f" in item and "from" not in item:
                item["from"] = item.pop("f")
            if "c" in item and "caption" not in item:
                item["caption"] = item.pop("c")
            if "from" in item and "to" in item:
                frm, to = str(item["from"]), str(item["to"])
                add_edge(frm, to, str(item.get("label") or ""))
                step = {"edge": f"{frm}->{to}"}
                for k in ("caption", "detail", "focus", "optional", "reveal", "highlight"):
                    if k in item:
                        step[k] = item[k]
                out_s.append(step)
            else:
                # classic {edge: "a->b"} — still ensure the edge exists
                ref = item.get("edge")
                if isinstance(ref, str) and "->" in ref:
                    a, b = (p.strip() for p in ref.split("->", 1))
                    add_edge(a, b)
                out_s.append(item)
        data["steps"] = out_s

    data["edges"] = edges

    # teaching boards: groups with blocks; nodes optional
    if boards.has_boards(data):
        data.setdefault("nodes", [])
        data.setdefault("edges", [])
        boards.expand_board_steps(data)

    # defaults the model need not fill
    data.setdefault("direction", "TB")
    if data.get("theme") not in THEMES:
        data["theme"] = THEMES[0]
    return data


def load_content_json(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid json: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("content json must be an object")
    fmt = str(data.get("format") or "")
    if fmt and fmt != CONTENT_FORMAT and fmt not in LEGACY_FORMATS:
        raise ValueError(f"unsupported format: {fmt}")
    data = expand_content(data)
    if "chart" in data:
        data["chart"] = charts.validate_chart(data["chart"])
        data.setdefault("nodes", [])
        data.setdefault("edges", [])
    if boards.has_boards(data):
        data.setdefault("nodes", [])
        data.setdefault("edges", [])
    if "nodes" not in data:
        raise ValueError("content json needs nodes (or a chart / boards)")
    data.setdefault("edges", [])
    if not data["nodes"] and "chart" not in data and not boards.has_boards(data):
        raise ValueError("content json needs at least one node, a chart, or board blocks")
    if not isinstance(data["nodes"], list) or not isinstance(data["edges"], list):
        raise ValueError("nodes and edges must be arrays")
    for node in data["nodes"]:
        if not isinstance(node, dict):
            continue
        for bad in ("x", "y", "width", "height", "points"):
            if bad in node:
                raise ValueError(f"content-only node must not include {bad}")
    data["format"] = CONTENT_FORMAT
    return data


def _as_list(value) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v)]
    return [str(value)]


def build_steps(edges: list, labels: list, visible: list, raw_steps=None, start=None, names=None) -> dict:
    """Resolve the play order.

    edges   : [(from, to)] in source order
    labels  : edge label per edge ("" if none)
    visible : bool per edge (invisible ~~~ links are never steps)
    raw_steps: optional JSON `steps` array. Each item is an edge index, "a->b",
               or {"edge"|"edges", "focus", "caption", "optional"}.
    Without `steps`, every visible edge is one step in source order.
    """
    keys = edge_keys(edges)
    names = names or {}

    def nm(node_id: str) -> str:
        return plain_label(str(names.get(node_id) or node_id)) or node_id

    def find(ref) -> int:
        if isinstance(ref, bool):
            raise ValueError(f"bad step edge: {ref!r}")
        if isinstance(ref, int):
            if 0 <= ref < len(edges):
                return ref
            raise ValueError(f"step edge index out of range: {ref}")
        text = str(ref).strip()
        if "->" not in text:
            raise ValueError(f"step edge must be index or 'from->to': {ref!r}")
        left, right = (part.strip() for part in text.split("->", 1))
        for index, pair in enumerate(edges):
            if pair == (left, right):
                return index
        raise ValueError(f"step edge not found: {text}")

    steps = []
    if raw_steps:
        if not isinstance(raw_steps, list):
            raise ValueError("steps must be an array")
        for item in raw_steps:
            if isinstance(item, dict):
                refs = item.get("edges")
                if refs is None:
                    refs = [item["edge"]] if "edge" in item else []
                if not isinstance(refs, list):
                    refs = [refs]
                idx = [find(r) for r in refs]
                focus = _as_list(item.get("focus"))
                caption = str(item.get("caption") or "")
                optional = bool(item.get("optional"))
                extra = {k: item[k] for k in ("detail", "reveal", "highlight") if k in item}
            else:
                idx = [find(item)]
                focus, caption, optional, extra = [], "", False, {}
            if not focus:
                focus = [edges[i][1] for i in idx]
            if not caption and idx:
                caption = labels[idx[0]] or f"{nm(edges[idx[0]][0])} → {nm(edges[idx[0]][1])}"
            step = {
                "edges": [keys[i] for i in idx],
                "nodes": sorted({n for i in idx for n in edges[i]}),
                "focus": focus,
                "caption": caption,
                "optional": optional,
            }
            if "detail" in extra:
                step["detail"] = str(extra["detail"] or "")
            if "reveal" in extra:
                step["reveal"] = extra["reveal"]
            if "highlight" in extra:
                step["highlight"] = extra["highlight"]
            steps.append(step)
    else:
        for i, (left, right) in enumerate(edges):
            if not visible[i]:
                continue
            steps.append({
                "edges": [keys[i]],
                "nodes": [left, right],
                "focus": [right],
                "caption": (f"{nm(left)} → {nm(right)}：{labels[i]}" if labels[i] else f"{nm(left)} → {nm(right)}"),
                "optional": False,
            })
    start_nodes = _as_list(start)
    if not start_nodes and steps:
        first = steps[0]["edges"][0] if steps[0]["edges"] else None
        if first is not None:
            start_nodes = [edges[keys.index(first)][0]]
    return {
        "steps": steps,
        "start": start_nodes,
        "edges": [
            {"key": keys[i], "from": l, "to": r, "visible": visible[i]}
            for i, (l, r) in enumerate(edges)
        ],
    }


def parse_flowchart(src: str):
    direction = "LR"
    nodes = {}
    order = []
    edges = []
    labels = []
    visible = []

    def absorb(token: str):
        token = token.strip()
        found = NODE_RE.match(token)
        if not found:
            return None
        node_id = found.group(1)
        label = next((g for g in found.groups()[1:] if g), None)
        if node_id not in nodes:
            nodes[node_id] = label or node_id
            order.append(node_id)
        elif label:
            nodes[node_id] = label
        return node_id

    for raw in src.splitlines():
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        low = line.lower()
        if low.startswith("flowchart") or low.startswith("graph"):
            parts = line.split()
            if len(parts) > 1:
                direction = parts[1].upper()
            continue
        if low == "end" or low.startswith(("classdef", "class ", "style ", "linkstyle", "subgraph", "end ", "direction", "click ")):
            continue
        found = EDGE_RE.match(line)
        if found:
            left = absorb(found.group(1))
            right = absorb(found.group(4))
            if left and right:
                edges.append((left, right))
                labels.append(plain_label(found.group(3) or ""))
                visible.append(found.group(2) != "~~~")
            continue
        absorb(line)
    return direction, order, nodes, edges, labels, visible


def fence_body(fence: str) -> str:
    lines = fence.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip("\n")


def xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def text_width(label: str) -> int:
    width = 0
    for char in label:
        width += 26 if ord(char) > 127 else 15
    return max(220, width + 96)


def plain_label(label: str) -> str:
    raw = (label or "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        raw = raw[1:-1]
    raw = re.split(r"<br\s*/?>", raw, maxsplit=1)[0]
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = raw.replace("#quot;", '"').replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
    return raw.strip()


def fallback_svg(direction: str, order: list, nodes: dict, edges: list, visible: list) -> str:
    """Plain SVG used until mermaid loads (or if the CDN is unreachable)."""
    boxes = {}
    node_h = 84
    if direction in {"TB", "TD"}:
        y = 40
        max_w = 0
        for node_id in order:
            width = text_width(plain_label(nodes[node_id]))
            boxes[node_id] = (48, y, width, node_h)
            max_w = max(max_w, width)
            y += node_h + 64
        canvas_w = max(max_w + 96, 640)
        canvas_h = max(y, 240)
    else:
        x = 40
        for node_id in order:
            width = text_width(plain_label(nodes[node_id]))
            boxes[node_id] = (x, 72, width, node_h)
            x += width + 120
        canvas_w = max(x, 760)
        canvas_h = 240
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas_w} {canvas_h}" role="img">']
    for key, (left, right), show in zip(edge_keys(edges), edges, visible):
        if not show:
            continue
        ax, ay, aw, ah = boxes[left]
        bx, by, bw, bh = boxes[right]
        if direction in {"TB", "TD"}:
            x1, y1, x2, y2 = ax + aw / 2, ay + ah, bx + bw / 2, by
        else:
            x1, y1, x2, y2 = ax + aw, ay + ah / 2, bx, by + bh / 2
        parts.append(
            f'<path class="fallback-edge" data-edge-key="{xml_escape(key)}" '
            f'd="M {x1:.1f} {y1:.1f} L {x2:.1f} {y2:.1f}" fill="none" stroke="#111111" stroke-width="1.2"/>'
        )
    for node_id in order:
        x, y, width, height = boxes[node_id]
        label = xml_escape(plain_label(nodes[node_id]))
        parts.append(
            f'<g class="node" data-node-id="{xml_escape(node_id)}">'
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="3" fill="#ffffff" stroke="#111111" stroke-width="1.2"/>'
            f'<text x="{x + width / 2:.1f}" y="{y + height / 2 + 7:.1f}" text-anchor="middle" '
            f'font-family="Latin Modern Roman, Noto Serif, Noto Serif CJK SC, serif" font-size="18" fill="#111111">{label}</text>'
            f"</g>"
        )
    parts.append("</svg>")
    return "".join(parts)


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def md_to_html(text: str) -> str:
    cleaned = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    if not cleaned:
        return ""
    out = []
    for block in re.split(r"\n\s*\n", cleaned):
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if len(lines) >= 2 and "|" in lines[0] and re.match(r"^\s*\|?[\s|:-]+\|?\s*$", lines[1]):
            out.append(_table_html(lines))
            continue
        if block.startswith("# "):
            out.append(f"<h1>{html_escape(block[2:].strip())}</h1>")
        elif block.startswith("## "):
            out.append(f"<h2>{html_escape(block[3:].strip())}</h2>")
        elif block.startswith("### "):
            out.append(f"<h3>{html_escape(block[4:].strip())}</h3>")
        else:
            out.append(f"<p>{html_escape(block).replace(chr(10), '<br>')}</p>")
    return "\n".join(out)


def _table_html(lines: list) -> str:
    def cells(line: str) -> list:
        raw = line.strip()
        if raw.startswith("|"):
            raw = raw[1:]
        if raw.endswith("|"):
            raw = raw[:-1]
        return [c.strip() for c in raw.split("|")]

    header = cells(lines[0])
    body_rows = [cells(line) for line in lines[2:] if line.strip() and "|" in line]
    parts = ['<table class="rules">', "<thead><tr>"]
    for cell in header:
        parts.append(f"<th>{html_escape(cell)}</th>")
    parts.append("</tr></thead><tbody>")
    for row in body_rows:
        parts.append("<tr>")
        for index, cell in enumerate(row):
            tag = "th" if index == 0 else "td"
            parts.append(f"<{tag}>{html_escape(cell)}</{tag}>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


ROLE_SVG = {  # fill, stroke, text, dash — colours come from theme CSS vars (Tufte pastels)
    "agent": ("var(--role-agent-fill)", "var(--role-agent-stroke)", "var(--role-agent-ink)", ""),
    "keep": ("var(--role-keep-fill)", "var(--role-keep-stroke)", "var(--role-keep-ink)", ""),
    "reset": ("var(--role-reset-fill)", "var(--role-reset-stroke)", "var(--role-reset-ink)", ""),
    "human": ("var(--role-human-fill)", "var(--role-human-stroke)", "var(--role-human-ink)", ""),
    "editable": ("var(--role-edit-fill)", "var(--role-edit-stroke)", "var(--role-edit-ink)", ""),
    "locked": ("var(--role-lock-fill)", "var(--role-lock-stroke)", "var(--role-lock-ink)", ""),
    "step": ("var(--node-fill)", "var(--ink)", "var(--ink)", ""),
    "decision": ("var(--node-fill)", "var(--ink)", "var(--ink)", ""),
    "dashed": ("var(--node-fill)", "var(--ink)", "var(--ink)", "5 4"),
    "caption": ("transparent", "transparent", "var(--muted)", ""),
}


STEP_MARK_RE = re.compile(r"^\s*[\u2460-\u2473]\s*")


def lane_svg(data: dict, step_no: dict | None = None) -> str:
    """Own renderer for region (group / lane) diagrams; geometry from layout.py."""
    L = lay.layout(data)
    boxes = L["nodes"]
    step_no = step_no or {}
    keys = edge_keys([(str(e["from"]), str(e["to"])) for e in lay._edges(data)])
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {L["width"]} {L["height"]}" role="img" class="cv-lanes" '
        f'style="max-width:{round(L["width"] * 1.1)}px">',
        '<defs><marker id="cv-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="cv-arrowhead"/></marker></defs>',
    ]
    heads = []
    plates = []  # group title areas: edge labels keep clear of them too
    for g in L["groups"]:
        if not g["title"] and g["id"] == "_loose":
            continue
        x, y, w, h = g["x"], g["y"], g["w"], g["h"]
        role = xml_escape(g["role"] or "plain")
        letter = xml_escape(g.get("letter") or "")
        has_sub = bool(g["subtitle"])
        ty = y + 24 if has_sub else y + 31
        sy = y + 40
        sub = (f'<text x="{x + 48}" y="{sy}" class="cv-group-sub">{xml_escape(g["subtitle"])}</text>'
               if has_sub else "")
        badge = (f'<rect x="{x + 12}" y="{y + 12}" width="26" height="26" rx="4" class="cv-group-badge"/>'
                 f'<text x="{x + 26}" y="{y + 32}" text-anchor="middle" class="cv-group-letter">{letter}</text>') if letter else ""
        out.append(
            f'<g class="cv-group-under grole-{role}" data-group-id="{xml_escape(g["id"])}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" class="cv-group-fill"/>'
            f'<rect x="{x + 1}" y="{y + 1}" width="{w - 2}" height="{lay.HEAD - 4}" rx="3" class="cv-group-headbg"/></g>'
        )
        plate_w = max(lay.text_px(g["title"]), lay.text_px(g["subtitle"] or "") * 0.82) * 1.25 + 14
        plate = (f'<rect x="{x + 42}" y="{y + 12}" width="{plate_w:.0f}" height="{(34 if has_sub else 26)}" rx="3" '
                 f'class="cv-group-plate"/>')
        plates.append((x + 8, y + 8, plate_w + 38, 38 if has_sub else 30))
        heads.append(
            f'<g class="cv-group grole-{role}" data-group-id="{xml_escape(g["id"])}" data-letter="{letter}" data-members="{xml_escape(" ".join(g["members"]))}">'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" class="cv-group-box"/>'
            f'<g class="cv-group-head">'
            f'{plate}{badge}<text x="{x + 48}" y="{ty}" class="cv-group-title">{xml_escape(g["title"])}</text>{sub}</g></g>'
        )
    labels = []
    placed = []  # labels already put down; later ones step around them
    for key, e in zip(keys, lay._edges(data)):
        style = str(e.get("style") or "solid").lower()
        left, right = str(e["from"]), str(e["to"])
        if style == "invisible" or left not in boxes or right not in boxes:
            continue
        pts = lay.route(L, left, right)
        d = "M " + " L ".join(f"{px:.1f} {py:.1f}" for px, py in pts)
        dash = ' stroke-dasharray="6 4"' if style == "dashed" else ""
        out.append(f'<path class="cv-link" data-edge-key="{key}" d="{d}" fill="none" marker-end="url(#cv-arrow)"{dash}/>')
        label = STEP_MARK_RE.sub("", str(e.get("label") or ""))
        n = step_no.get(key)
        if label or n:
            tw = lay.text_px(label) * 0.95 + (10 if label else 0)
            bw = 20 if n else 0
            gap = 6 if (n and label) else 0
            total = tw + bw + gap
            mx, my = lay.label_point(pts, list(boxes.values()) + plates + placed, (total + 8, 22))
            placed.append((mx - total / 2 - 4, my - 11, total + 8, 22))
            x0 = mx - total / 2
            parts = [f'<g class="edgeLabel" data-edge-key="{key}">']
            if label or n:
                parts.append(f'<rect x="{x0 - 4:.1f}" y="{my - 11:.1f}" width="{total + 8:.1f}" height="22" rx="11" class="cv-label-bg"/>')
            if n:
                parts.append(f'<rect x="{x0:.1f}" y="{my - 9:.1f}" width="20" height="18" rx="9" class="cv-step-badge"/>'
                             f'<text x="{x0 + 10:.1f}" y="{my + 4:.1f}" text-anchor="middle" class="cv-step-num">{n}</text>')
            if label:
                parts.append(f'<text x="{x0 + bw + gap + 2:.1f}" y="{my + 4:.1f}" text-anchor="start" class="cv-edge-text">{xml_escape(label)}</text>')
            parts.append("</g>")
            labels.append("".join(parts))
    out.extend(heads)  # frames + titles above the edges that cross them
    for n in data.get("nodes") or []:
        if not isinstance(n, dict) or str(n.get("id")) not in boxes:
            continue
        nid = str(n["id"])
        x, y, w, h = boxes[nid]
        role = str(n.get("role") or "plain")
        fill, stroke, ink, dash = ROLE_SVG.get(role, ("var(--node-fill)", "var(--ink)", "var(--ink)", ""))
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        lines = lay.label_lines(n.get("label") or nid)
        parts = [
            f'<g class="node role-{xml_escape(role)}" data-node-id="{xml_escape(nid)}">'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" style="fill:{fill};stroke:{stroke}" '
            f'class="label-container"{dash_attr}/>'
        ]
        top = y + h / 2 - (len(lines) - 1) * lay.LINE_H / 2 + 5
        for i, line in enumerate(lines):
            klass = "cv-node-title" if i == 0 else "cv-node-sub"
            parts.append(
                f'<text x="{x + w / 2:.1f}" y="{top + i * lay.LINE_H:.1f}" text-anchor="middle" class="{klass}" '
                f'style="fill:{ink}">{xml_escape(line)}</text>'
            )
        parts.append("</g>")
        out.append("".join(parts))
    out.extend(labels)  # labels on top of nodes and edges
    out.append("</svg>")
    return "".join(out)


def pick_theme(*candidates) -> str:
    for c in candidates:
        if c and str(c) in THEMES:
            return str(c)
    return THEMES[0]


PLAYBACK_DEFAULT = {"autoplay": False, "interval_ms": 1800, "loop": False}


def normalize_playback(raw) -> dict:
    """Optional content field `playback`; bad values fall back to defaults."""
    out = dict(PLAYBACK_DEFAULT)
    if not isinstance(raw, dict):
        return out
    if isinstance(raw.get("autoplay"), bool):
        out["autoplay"] = raw["autoplay"]
    if isinstance(raw.get("loop"), bool):
        out["loop"] = raw["loop"]
    ms = raw.get("interval_ms")
    if isinstance(ms, int) and not isinstance(ms, bool):
        out["interval_ms"] = max(300, min(60000, ms))
    return out




def board_cols(data: dict) -> int:
    """Equal-cell sheet columns: explicit cols, else 2–3 from board count."""
    raw = data.get("cols") or data.get("columns")
    if isinstance(raw, int) and 1 <= raw <= 6:
        return raw  # STE overview uses 4
    n = 0
    for g in data.get("groups") or []:
        if isinstance(g, dict) and (g.get("blocks") or g.get("b")):
            n += 1
    if n <= 2:
        return max(n, 1)
    if n <= 4:
        return 2
    return 3


def rules_html(rules) -> str:
    """Bottom rules table removed from HTML display (user: no footer summary table)."""
    return ""
    if not isinstance(rules, list) or not rules:  # noqa: unreachable — kept for API compat
        return ""
    rows = []
    for item in rules:
        if isinstance(item, dict):
            rule = str(item.get("rule") or item.get("name") or item.get("r") or "")
            practice = str(item.get("practice") or item.get("in_practice") or item.get("p") or item.get("text") or "")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            rule, practice = str(item[0]), str(item[1])
        else:
            continue
        if not rule and not practice:
            continue
        rows.append(f"<tr><th>{html_escape(rule)}</th><td>{html_escape(practice)}</td></tr>")
    if not rows:
        return ""
    return (
        '<section class="cv-rules" aria-label="rules">'
        '<h2 class="cv-rules-title">Rules the agent works under</h2>'
        '<table class="rules"><thead><tr><th>Rule</th><th>In practice</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></section>'
    )


def rel_src(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def assemble_page(src: str, body: str, order: list, nodes: dict, edges: list, visible: list,
                  direction: str, play: dict, before_html: str, after_html: str,
                  theme: str = THEMES[0], custom_svg: str | None = None,
                  chart: dict | None = None, wide: bool = False, page_regions: list | None = None,
                  playback: dict | None = None, mode: str = "page", sections: bool = False,
                  boards_html: str | None = None, orientation: str = "landscape",
                  display: str = "sheet", cols: int | None = None,
                  title: str = "", meta: dict | None = None, served: bool = False) -> str:
    video = mode == "video"
    if video:
        theme = VIDEO_THEME
    payload = {
        "mode": "video" if video else "page",
        "playback": playback or dict(PLAYBACK_DEFAULT),
        "chart": None if chart is None else {
            "type": chart["type"], "categories": chart["categories"], "unit": chart["unit"],
            "series": [{"name": x["name"], "values": x["values"]} for x in chart["series"]],
            "sample": chart["sample"],
        },
        "hasDiagram": bool(order) or bool(boards_html),
        "wide": wide or bool(boards_html),
        "sections": sections or bool(boards_html),
        "theme": theme,
        "boards": bool(boards_html),
        "orientation": orientation if orientation in {"landscape", "portrait"} else "landscape",
        "display": display if display in {"sheet", "deck", "lesson"} else "sheet",
        "cols": cols,
        "title": title,
        "meta": {str(k): ("、".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v))
                 for k, v in (meta or {}).items()} if isinstance(meta, dict) else {},
        "served": served,
        "useMermaid": custom_svg is None and not boards_html,
        "src": src,
        "mermaid": body if not boards_html else "",
        "nodes": [{"id": node_id, "label": plain_label(nodes[node_id])} for node_id in order],
        "edges": play["edges"],
        "steps": play["steps"],
        "start": play["start"],
        "fallback": boards_html if boards_html is not None else (custom_svg if custom_svg is not None else fallback_svg(direction, order, nodes, edges, visible)),
    }
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    page = PAGE.replace("/*DATA*/", data).replace("/*THEME*/", theme, 1).replace("<!--BEFORE-->", before_html)
    page = page.replace("/*MODE*/", "video" if video else "page", 1)
    page = page.replace("/*ORIENTATION*/", payload["orientation"], 1)
    page = page.replace("/*DISPLAY*/", payload["display"], 1)
    if video:
        page = page.replace("<!--VIDEO-->", VIDEO_EXTRA)
    chart_block = charts.chart_html(chart) if chart else ""
    regions = {g["id"]: g for g in page_regions or []}
    chart_region = next((g for g in regions.values() if "@chart" in g["members"]), None)
    explain_region = next((g for g in regions.values() if "@caption" in g["members"] or "@notes" in g["members"]), None)
    if chart_region and chart_block:
        chart_block = region_open(chart_region, "chart-region") + chart_block + "</section>"
    if explain_region and "@notes" in explain_region["members"]:
        page = page.replace("<!--EXPLAIN_OPEN-->", region_open(explain_region, "explain-region"))
        page = page.replace("<!--EXPLAIN_CLOSE-->", "</section>")
        page = page.replace("<!--EXPLAIN_NOTES-->", f'<div class="prose-after">{after_html}</div>')
        page = page.replace('<div class="prose-after"><!--AFTER--></div>', "")
    return page.replace("<!--AFTER-->", after_html).replace("<!--CHART-->", chart_block)


def region_open(g: dict, dom_id: str) -> str:
    sub = f'<span class="cv-region-sub">{html_escape(g.get("subtitle") or "")}</span>' if g.get("subtitle") else ""
    return (f'<section class="cv-region cv-group grole-{html_escape(g.get("role") or "plain")}" id="{dom_id}" data-group-id="{html_escape(g["id"])}" data-letter="{html_escape(g.get("letter") or "")}">'
            f'<div class="cv-region-head"><span class="cv-group-badge cv-region-badge">{html_escape(g.get("letter") or "")}</span><span class="cv-region-title">{html_escape(g.get("title") or "")}</span>{sub}</div>')


def step_numbers(play: dict) -> dict:
    out = {}
    for i, st in enumerate(play["steps"]):
        for k in st["edges"]:
            out.setdefault(k, i + 1)
    return out


def use_lanes(data: dict) -> bool:
    mode = str(data.get("layout") or "").lower()
    if mode in {"mermaid", "lanes"}:
        return mode == "lanes"
    return bool(lay.resolve_groups(data))


def render_json_page(json_path: Path, theme: str | None = None, mode: str = "page", display: str | None = None,
                     orientation: str | None = None, served: bool = False) -> str:
    data = load_content_json(json_path)
    if display in {"sheet", "deck", "lesson"}:
        data["display"] = display
    if orientation in {"landscape", "portrait"}:
        data["orientation"] = orientation
    title = str(data.get("title") or json_path.stem)
    before = f"<h1>{html_escape(title)}</h1>"
    if data.get("subtitle"):
        before += f'<p class="cv-subtitle">{html_escape(str(data["subtitle"]))}</p>'
    notes = str(data.get("notes") or "").strip()
    # Never render bottom rules/summary table in HTML; optional short notes only.
    after = (f"<p>{html_escape(notes)}</p>" if notes else "")
    theme_picked = pick_theme(theme, data.get("theme"))
    ori = str(data.get("orientation") or "landscape").lower()
    if ori not in {"landscape", "portrait"}:
        ori = "landscape"
    disp = str(data.get("display") or "sheet").lower()
    if disp not in {"sheet", "deck", "lesson"}:
        disp = "sheet"
    ncols = board_cols(data)
    # dense teaching boards (blocks inside groups) — not sparse node graphs
    if boards.has_boards(data):
        letters = boards.board_letters(data)
        for g in data.get("groups") or []:
            if isinstance(g, dict) and g.get("id"):
                g["letter"] = letters.get(str(g["id"]), g.get("letter") or "")
        play = boards.board_play(data)
        if disp == "lesson":
            html = boards.render_lesson(data, letters)
        else:
            html = boards.render_boards(data, letters, cols=ncols)
        return assemble_page(rel_src(json_path), "", [], {}, [], [], "TB", play, before, after,
                             theme=theme_picked, custom_svg=None, boards_html=html,
                             chart=data.get("chart"), wide=True, sections=True,
                             playback=normalize_playback(data.get("playback")), mode=mode,
                             orientation=ori, display=disp, cols=ncols, title=title, meta=data.get("meta"), served=served)
    body = content_to_mermaid(data)
    direction, order, nodes, edges, _labels, visible = parse_flowchart(body)
    # labels/visibility straight from JSON (parse_flowchart only sees escaped text)
    json_edges = [e for e in data["edges"] if isinstance(e, dict) and e.get("from") and e.get("to")]
    labels = [str(e.get("label") or "") for e in json_edges]
    visible = [str(e.get("style") or "").lower() != "invisible" for e in json_edges]
    for node in data["nodes"]:
        if isinstance(node, dict) and node.get("id") in nodes:
            nodes[node["id"]] = str(node.get("label") or node["id"]).split("\n")[0]
    play = build_steps(edges, labels, visible, data.get("steps"), data.get("start"), nodes)
    chart = data.get("chart")
    for st in play["steps"]:
        if chart is None:
            st.pop("reveal", None)
            st.pop("highlight", None)
            continue
        if "reveal" in st:
            r = st["reveal"]
            if isinstance(r, bool) or not isinstance(r, int) or not 0 <= r <= len(chart["categories"]):
                raise ValueError(f"step reveal must be 0..{len(chart['categories'])}")
        if "highlight" in st:
            st["highlight"] = charts.category_index(chart, st["highlight"])
    return assemble_page(rel_src(json_path), body, order, nodes, edges, visible, direction, play, before, after,
                         theme=theme_picked,
                         custom_svg=(lane_svg(data, step_numbers(play)) if use_lanes(data) else None) if data["nodes"] else "",
                         chart=chart, page_regions=[dict(g, letter=lay.page_letters(data).get(str(g["id"]), "")) for g in data.get("groups") or [] if isinstance(g, dict) and any(str(m).startswith("@") for m in g.get("members") or [])],
                         wide=(lambda L: (L["width"] > 760, L.get("sections_mode")))(lay.layout(data))[0] if data["nodes"] and (use_lanes(data) or bool(data.get("groups"))) else False,
                         sections=bool(data.get("groups")) and not use_lanes(data),
                         playback=normalize_playback(data.get("playback")), mode=mode,
                         orientation=ori, display=disp, cols=ncols, title=title, meta=data.get("meta"), served=served)


def render_md_page(md_path: Path, theme: str | None = None, mode: str = "page") -> str:
    text = md_path.read_text(encoding="utf-8")
    start, end, _section = locate_fence(text, "flow")
    body = fence_body(text[start:end])
    direction, order, nodes, edges, labels, visible = parse_flowchart(body)
    play = build_steps(edges, labels, visible, names=nodes)
    return assemble_page(rel_src(md_path), body, order, nodes, edges, visible, direction, play,
                         md_to_html(text[:start]), md_to_html(text[end:]), theme=pick_theme(theme), mode=mode)


def render_page(path: Path, theme: str | None = None, mode: str = "page", display: str | None = None,
                orientation: str | None = None, served: bool = False) -> str:
    path = Path(path)
    if is_content_json(path):
        return render_json_page(path, theme, mode, display, orientation, served)
    return render_md_page(path, theme, mode)


VIDEO_EXTRA = r"""
<style>
  /* ---- 3b1b look (video only) ---- */
  body[data-theme="3b1b"] {
    --bg: #141414; --paper: transparent; --ink: #f2efe8; --muted: #9a9690; --line: #3a3a3a;
    --rule-thin: #2a2a2a; --node-fill: transparent; --label-bg: #141414;
    --group-fill: rgba(255,255,255,.03); --group-stroke: #3f3f3f; --group-head: transparent;
    --group-title: #d8d4cc; --group-sub: #8e8a82;
    --c-blue: #58c4dd; --c-green: #83c167; --c-red: #fc6255; --c-yellow: #ffff00;
    --c-teal: #5cd0b3; --c-gold: #f0ac5f; --c-grey: #888888;
    --bg-image: radial-gradient(ellipse at 50% 42%, #1c1c1c 0%, #141414 58%, #0e0e0e 100%);
    --bg-size: 100% 100%;
    --body-font: "Source Serif 4", "Noto Serif SC", "Latin Modern Roman", Georgia, serif;
    --ui: "Inter", "Noto Sans SC", system-ui, sans-serif;
    --font-body: var(--body-font); --font-display: var(--body-font);
    --ink-2: #c8c4bc; --line-strong: #8a867e; --surface: transparent; --surface-2: rgba(255,255,255,.05);
    --accent: var(--c-yellow); --accent-soft: rgba(255,255,0,.12); --accent-ink: #141414;
    --group-halo: transparent; --node-fill: transparent; --node-stroke: #c8c4bc;
    --chart-1: var(--c-blue); --chart-2: var(--c-gold); --chart-3: var(--c-green); --chart-hl: var(--c-yellow);
  }
  body[data-theme="3b1b"] #diagram .edgeLabel .cv-step-badge { fill: none !important; }
  body[data-theme="3b1b"] #diagram .edgeLabel .cv-label-bg { stroke: #3a3a3a !important; }
  body[data-theme="3b1b"] .cv-region { background: var(--group-fill); border-color: var(--group-stroke); box-shadow: none; }
  body[data-theme="3b1b"] .cv-group-under.cv-group-off { opacity: .38; }
  body[data-theme="3b1b"] .cv-group { --gc: #9b9b9b; }
  body[data-theme="3b1b"] .cv-group.grole-step { --gc: var(--c-blue); }
  body[data-theme="3b1b"] .cv-group.grole-keep { --gc: var(--c-green); }
  body[data-theme="3b1b"] .cv-group.grole-human,
  body[data-theme="3b1b"] .cv-group.grole-reset { --gc: var(--c-red); }
  body[data-theme="3b1b"] .cv-group.grole-agent { --gc: var(--c-yellow); }
  body[data-theme="3b1b"] .cv-group.grole-editable { --gc: var(--c-teal); }
  body[data-theme="3b1b"] .cv-group.grole-decision { --gc: var(--c-gold); }
  body[data-theme="3b1b"] .cv-group-badge { fill: none; stroke: var(--gc); stroke-width: 1.4px; }
  body[data-theme="3b1b"] .cv-group-letter { fill: var(--gc); font-size: 14px; }
  body[data-theme="3b1b"] .cv-group-title { fill: var(--gc); font-weight: 500; letter-spacing: .04em; font-size: 15px; font-variant: normal; }
  body[data-theme="3b1b"] .cv-group-sub { fill: #8e8a82; font-size: 12px; }
  body[data-theme="3b1b"] .cv-group-box { stroke-width: 1px; }
  body[data-theme="3b1b"] .cv-group.cv-group-on .cv-group-box { stroke: var(--gc); stroke-opacity: .7; }
  body[data-theme="3b1b"]:not(.cv-overview) .cv-group.cv-group-off { opacity: .38; }
  body[data-theme="3b1b"] .cv-step-badge { fill: none; stroke: #c8c4bc; stroke-width: 1.2px; }
  body[data-theme="3b1b"] .cv-step-num { fill: #ece8e0; }
  body[data-theme="3b1b"] #diagram .edgeLabel.cv-flow .cv-step-badge { stroke: var(--c-yellow); }
  body[data-theme="3b1b"] #diagram .edgeLabel.cv-flow .cv-step-num { fill: var(--c-yellow) !important; }
  body[data-theme="3b1b"] .frame { width: 100%; max-width: none; box-shadow: none; border: none; border-radius: 0; background: transparent; }
  body[data-theme="3b1b"] .prose-before > h1 {
    font-size: 2.6rem; font-weight: 500; border-bottom: 1px solid #333; letter-spacing: .01em; padding-bottom: .7rem;
  }
  body[data-theme="3b1b"] .prose-before > h1::after { display: none; }
  body[data-theme="3b1b"] #diagram g.node { --rc: var(--c-blue); }
  body[data-theme="3b1b"] #diagram g.node.keep, body[data-theme="3b1b"] #diagram g.node.role-keep { --rc: var(--c-green); }
  body[data-theme="3b1b"] #diagram g.node.reset, body[data-theme="3b1b"] #diagram g.node.role-reset,
  body[data-theme="3b1b"] #diagram g.node.human, body[data-theme="3b1b"] #diagram g.node.role-human { --rc: var(--c-red); }
  body[data-theme="3b1b"] #diagram g.node.agent, body[data-theme="3b1b"] #diagram g.node.role-agent { --rc: var(--c-yellow); }
  body[data-theme="3b1b"] #diagram g.node.editable, body[data-theme="3b1b"] #diagram g.node.role-editable { --rc: var(--c-teal); }
  body[data-theme="3b1b"] #diagram g.node.decision, body[data-theme="3b1b"] #diagram g.node.role-decision { --rc: var(--c-gold); }
  body[data-theme="3b1b"] #diagram g.node.dashed, body[data-theme="3b1b"] #diagram g.node.role-dashed { --rc: var(--c-grey); }
  body[data-theme="3b1b"] #diagram g.node rect, body[data-theme="3b1b"] #diagram g.node polygon,
  body[data-theme="3b1b"] #diagram g.node circle, body[data-theme="3b1b"] #diagram g.node ellipse,
  body[data-theme="3b1b"] #diagram g.node path, body[data-theme="3b1b"] #diagram g.node .label-container {
    fill: transparent !important; stroke: var(--rc) !important; stroke-width: 2px !important; filter: none !important;
  }
  body[data-theme="3b1b"] #diagram g.node text { fill: var(--rc) !important; font-size: 15px !important; }
  body[data-theme="3b1b"] #diagram g.node text.cv-node-sub { fill: #c8c4bc !important; font-size: 12.5px !important; opacity: 1; }
  body[data-theme="3b1b"] #diagram .nodeLabel, body[data-theme="3b1b"] #diagram .nodeLabel * { color: var(--rc) !important; }
  body[data-theme="3b1b"] #diagram g.node.cv-dim { opacity: .12; }
  body[data-theme="3b1b"] #diagram g.node.cv-lit { opacity: .48; }
  body[data-theme="3b1b"] #diagram g.node.cv-lit.cv-current,
  body[data-theme="3b1b"].cv-overview #diagram g.node.cv-lit { opacity: 1; }
  body[data-theme="3b1b"] #diagram g.node.cv-current rect,
  body[data-theme="3b1b"] #diagram g.node.cv-current polygon,
  body[data-theme="3b1b"] #diagram g.node.cv-current .label-container {
    stroke-width: 3.2px !important; stroke: var(--rc) !important; animation: none !important;
  }
  body[data-theme="3b1b"] #diagram .edgePath path, body[data-theme="3b1b"] #diagram path.flowchart-link,
  body[data-theme="3b1b"] #diagram .cv-link, body[data-theme="3b1b"] #diagram .fallback-edge {
    stroke: #b8b4ac !important; stroke-width: 1.4px !important;
  }
  body[data-theme="3b1b"] #diagram .cv-edge.cv-future { opacity: .1; }
  body[data-theme="3b1b"] #diagram .cv-edge.cv-done { opacity: .5; stroke: #a8a49c !important; }
  body[data-theme="3b1b"] #diagram .cv-edge.cv-flow, body[data-theme="3b1b"] #diagram .cv-edge.cv-flow-held {
    stroke: var(--c-yellow) !important; stroke-width: 2.4px !important; opacity: 1;
  }
  body[data-theme="3b1b"] #diagram .edgeLabel.cv-future { opacity: .1; }
  body[data-theme="3b1b"] #diagram .edgeLabel.cv-done { opacity: .55; }
  body[data-theme="3b1b"] #diagram .edgeLabel.cv-flow { opacity: 1; }
  body[data-theme="3b1b"] #diagram .edgeLabel.cv-flow text { fill: var(--c-yellow) !important; }
  body[data-theme="3b1b"] #diagram .edgeLabel, body[data-theme="3b1b"] #diagram .edgeLabel * { font-style: normal !important; font-size: 14px !important; }
  body[data-theme="3b1b"] .cv-arrowhead, body[data-theme="3b1b"] #diagram marker path { fill: #b8b4ac !important; }
  body[data-theme="3b1b"] #cv-packet { fill: var(--c-yellow) !important; }
  body[data-theme="3b1b"] .cv-label-bg { fill: #141414; stroke: #3a3a3a; }
  body[data-theme="3b1b"] .cv-edge-text { fill: #ece8e0; font-size: 14px; }
  body[data-theme="3b1b"] .cv-node-title { font-size: 16px; font-weight: 500; }
  body[data-theme="3b1b"] .caption-box {
    border: none !important; text-align: center; font-size: 1.55rem; line-height: 1.45; min-height: 0; margin-top: .5rem;
    font-family: var(--body-font);
  }
  body[data-theme="3b1b"] .caption-box .cap { font-style: normal; color: var(--ink); font-weight: 500; }
  body[data-theme="3b1b"] .caption-box .counter { display: block; font-size: .95rem; color: var(--muted); margin-bottom: .25rem; font-family: var(--ui); }
  body[data-theme="3b1b"] .caption-box pre.detail {
    display: inline-block; text-align: left; font-size: .95rem; border-left-color: var(--c-yellow); color: #d8d4cc; background: transparent;
  }

  /* video frame layout */
  body[data-mode="video"], body[data-mode="video"] * { animation: none !important; transition: none !important; }
  body[data-mode="video"] {
    width: 1920px; height: 1080px; overflow: hidden; margin: 0; cursor: none;
  }
  body[data-mode="video"] main {
    max-width: none !important; width: 1920px !important; height: 1080px !important;
    margin: 0 !important; padding: 48px 96px 40px !important; box-sizing: border-box;
    display: flex; flex-direction: column;
  }
  body[data-mode="video"] h1 { font-size: 58px !important; margin: 0 0 18px !important; letter-spacing: .01em; }
  body[data-mode="video"] #stage, body[data-mode="video"] #stage.wide {
    flex: 1 1 auto; min-height: 0; width: auto !important; left: auto !important;
    transform: none !important; margin: 0 !important; position: relative;
  }
  body[data-mode="video"] #diagram { position: absolute; inset: 0; }
  body[data-mode="video"] #diagram svg, body[data-mode="video"] #diagram svg.cv-lanes {
    width: 100% !important; height: 100% !important; max-width: none !important; max-height: none !important; overflow: hidden !important;
  }
  body[data-mode="video"] .bar, body[data-mode="video"] .prose-after, body[data-mode="video"] .prose-before p { display: none !important; }
  body[data-mode="video"] .caption-box {
    font-size: 42px !important; line-height: 1.35 !important; height: 168px; margin-top: 14px !important; overflow: hidden;
  }
  body[data-mode="video"] .caption-box .counter { font-size: 24px !important; letter-spacing: .1em; margin-bottom: 8px !important; }
  body[data-mode="video"] .caption-box pre.detail { font-size: 24px !important; margin-top: 10px !important; max-height: 2.8em; overflow: hidden; }
  body[data-mode="video"] #cv-packet { fill: var(--c-yellow); }
  body[data-mode="video"] #diagram .cv-edge.cv-flow { stroke-dasharray: none; }
  #cv-region-tag {
    position: absolute; right: 0; top: 0; z-index: 3; display: flex; gap: 18px; pointer-events: none;
  }
  #cv-region-tag .tag {
    display: flex; align-items: center; gap: 12px; font-size: 28px; letter-spacing: .04em; color: var(--gc);
    background: rgba(20,20,20,.88); padding: 8px 18px 8px 10px; border: 1px solid var(--gc); border-radius: 12px;
    font-family: var(--body-font);
  }
  #cv-region-tag .tag b {
    display: inline-block; min-width: 40px; height: 40px; line-height: 40px; text-align: center;
    border: 1.5px solid var(--gc); border-radius: 10px; font-variant: normal; font-weight: 500; font-family: var(--ui);
  }
</style>
<script>
  (function () {
    if (!VIDEO) return;
    const N = STEPS.length;
    const T = { intro: 1.8, step: Math.max(1.7, (DATA.playback && DATA.playback.interval_ms ? DATA.playback.interval_ms : 1800) / 1000 + 1.0), outro: 2.4 };
    const duration = T.intro + N * T.step + T.outro;
    const clamp = (x) => Math.max(0, Math.min(1, x));
    const ease = (f) => (f < .5 ? 4 * f * f * f : 1 - Math.pow(-2 * f + 2, 3) / 2);
    const lerp = (a, b, e) => a.map((v, i) => v + (b[i] - v) * e);
    let targets = null, curK = -1, touched = [];
    const newAt = [];
    const openCap = (DATA.nodes && DATA.nodes.length)
      ? ("共 " + N + " 步 · " + (document.querySelector(".prose-before h1")?.textContent || "").trim())
      : "";

    function fitOverview(svg, box) {
      // Expand the overview viewBox so the diagram fills the 16:9 stage with comfortable padding.
      const [fx, fy, fw, fh] = box;
      const ratio = (svg.clientWidth || 16) / Math.max(1, svg.clientHeight || 9);
      let w = fw, h = fh;
      if (w / h < ratio) w = h * ratio; else h = w / ratio;
      const pad = Math.max(w, h) * 0.06;
      w += 2 * pad; h += 2 * pad;
      return [fx + fw / 2 - w / 2, fy + fh / 2 - h / 2, w, h];
    }

    function init() {
      const svg = svgEl();
      if (!svg) return false;
      const raw = readBox(svg);
      if (!raw) return false;
      fullBox = fitOverview(svg, raw);
      setBox(svg, fullBox);
      targets = [];
      for (let k = 0; k <= N; k++) {
        step = k;
        setBox(svg, fullBox);
        targets.push(k === 0 ? fullBox.slice() : (focusBox(svg) || fullBox).slice());
      }
      for (let k = 1; k <= N; k++) {
        const a = stateAt(k - 1), b = stateAt(k);
        const before = new Set([...a.lit, ...a.current]);
        newAt[k] = [...b.lit, ...b.current].filter((id) => !before.has(id));
      }
      step = 0; curK = -1;
      return true;
    }

    function at(t) {
      if (t < T.intro) return { k: 0, p: t / T.intro, outro: false };
      const end = T.intro + N * T.step;
      if (t >= end) return { k: N, p: (t - end) / T.outro, outro: true };
      const k = Math.min(N, Math.floor((t - T.intro) / T.step) + 1);
      return { k, p: (t - T.intro - (k - 1) * T.step) / T.step, outro: false };
    }

    function untouch() {
      touched.forEach(([el, kind]) => {
        if (kind === "style") el.removeAttribute("style");
        if (kind === "len") { el.removeAttribute("pathLength"); el.removeAttribute("style"); }
        if (kind === "flow") el.classList.remove("cv-flow");
      });
      touched = [];
    }

    function render(t) {
      if (!targets && !init()) return;
      const { k, p, outro } = at(Math.max(0, Math.min(duration, t)));
      if (k !== curK) { step = k; overviewMode = false; paint(false); curK = k; }
      untouch();
      const svg = svgEl();
      let box = fullBox;
      if (outro) box = lerp(targets[N], fullBox, ease(clamp(p / 0.5)));
      else if (k > 0) box = lerp(targets[k - 1], targets[k], ease(clamp(p / 0.38)));
      setBox(svg, box);
      const overview = k === 0 || (outro && p > 0.22);
      document.body.classList.toggle("cv-overview", overview);
      host.setAttribute("data-camera-mode", overview ? "overview" : "focus");
      const cap = document.getElementById("cap"), counter = document.getElementById("counter");
      const capBox = document.getElementById("caption");
      if (k === 0) {
        counter.textContent = N ? "0 / " + N : "";
        cap.textContent = openCap || "开始";
        capBox.style.opacity = String(clamp((p - 0.08) / 0.2) * (1 - clamp((p - 0.82) / 0.15)));
      } else {
        capBox.style.opacity = outro ? String(1 - clamp(p / 0.35)) : String(clamp((p - 0.05) / 0.12));
      }
      const dot = svg.querySelector("#cv-packet");
      if (dot) dot.setAttribute("r", "0");
      regionTags(k, !outro && k > 0);
      tagBox.style.opacity = (outro || k === 0) ? "0" : String(clamp((p - 0.05) / 0.14));
      if (k === 0 || outro) return;
      const it = STEPS[k - 1];
      const dp = ease(clamp((p - 0.14) / 0.42));
      it.edges.forEach((key) => {
        host.querySelectorAll('path.cv-edge[data-edge-key="' + CSS.escape(key) + '"]').forEach((path, i) => {
          const L = path.getTotalLength ? path.getTotalLength() : 0;
          if (!L) return;
          path.classList.add("cv-flow"); touched.push([path, "flow"]);
          path.style.strokeDasharray = L + " " + L;
          path.style.strokeDashoffset = String(L * (1 - dp));
          if (dp < 0.98) path.style.markerEnd = "none";
          touched.push([path, "style"]);
          if (i === 0 && dot && dp > 0 && dp < 1) {
            const pt = path.getPointAtLength(dp * L);
            dot.setAttribute("cx", pt.x); dot.setAttribute("cy", pt.y); dot.setAttribute("r", "7");
          }
        });
        host.querySelectorAll('.edgeLabel[data-edge-key="' + CSS.escape(key) + '"]').forEach((lab) => {
          lab.classList.add("cv-flow"); touched.push([lab, "flow"]);
          lab.style.opacity = String(clamp((p - 0.28) / 0.18)); touched.push([lab, "style"]);
        });
      });
      const dn = ease(clamp((p - 0.4) / 0.32));
      (newAt[k] || []).forEach((id) => {
        const g = host.querySelector('g.node[data-node-id="' + CSS.escape(id) + '"]');
        if (!g) return;
        g.querySelectorAll("rect, polygon, path, circle, ellipse").forEach((sh) => {
          sh.setAttribute("pathLength", "1");
          sh.style.strokeDasharray = "1 1";
          sh.style.strokeDashoffset = String(1 - dn);
          touched.push([sh, "len"]);
        });
        g.querySelectorAll("text, foreignObject").forEach((tx) => {
          tx.style.opacity = String(clamp((p - 0.58) / 0.18)); touched.push([tx, "style"]);
        });
      });
    }

    const tagBox = document.createElement("div");
    tagBox.id = "cv-region-tag";
    document.getElementById("stage").appendChild(tagBox);
    let tagKey = "";
    function regionTags(k, show) {
      let html = "";
      if (show && k > 0) {
        const it = STEPS[k - 1];
        host.querySelectorAll("g.cv-group[data-members]").forEach((g) => {
          const members = (g.getAttribute("data-members") || "").split(" ");
          if (!it.focus.some((f) => members.includes(f))) return;
          const title = g.querySelector(".cv-group-title");
          const role = [...g.classList].find((c) => c.startsWith("grole-")) || "grole-plain";
          html += '<span class="tag cv-group ' + role + '"><b>' + (g.getAttribute("data-letter") || "") + "</b>" +
            (title ? title.textContent : "").replace(/[<&]/g, "") + "</span>";
        });
      }
      if (html !== tagKey) { tagBox.innerHTML = html; tagKey = html; }
    }

    window.cvVideo = { fps: 30, width: 1920, height: 1080, get duration() { return duration; }, timing: T, init, render };
  })();
</script>
"""


PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1440, initial-scale=1">
<title>canvas</title>
<style>
  /* =====================================================================
     Design tokens. Every rule below reads these; a theme only swaps values.
     book (default) · product · sketch.  3b1b lives in VIDEO_EXTRA only.
     ===================================================================== */
  body {
    --bg: #f6f2ea;
    --bg-image: none;
    --bg-size: auto;
    --paper: transparent;
    --surface: #fffdf8;
    --surface-2: #f2ece1;
    --ink: #1f1b16;
    --ink-2: #4b4339;
    --muted: #8a8072;
    --line: #e3dacb;
    --line-strong: #2a241d;
    --accent: #b8431e;
    --accent-ink: #fffdf8;
    --accent-soft: #f6e2d7;
    --ok: #3d6e47;
    --bad: #b8431e;
    --warn: #a86b12;
    --font-display: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, "Songti SC", "Noto Serif SC", "Source Han Serif SC", STSong, serif;
    --font-body: -apple-system, "Helvetica Neue", "PingFang SC", "Noto Sans SC", "Microsoft YaHei", sans-serif;
    --font-mono: "SF Mono", "JetBrains Mono", Menlo, Consolas, ui-monospace, monospace;
    --title-weight: 600;
    --title-align: center;
    --radius-sm: 2px;
    --radius: 3px;
    --radius-lg: 4px;
    --border-w: 1px;
    --shadow-sm: none;
    --shadow: 0 1px 0 rgba(42, 36, 29, .04);
    --shadow-lg: 0 1px 0 rgba(42, 36, 29, .05), 0 24px 48px -32px rgba(60, 40, 20, .28);
    --node-fill: #fffdf8;
    --node-stroke: #2a241d;
    --node-stroke-w: 1px;
    --node-radius: 2px;
    --label-bg: #f6f2ea;
    --bar-fill: #2a241d;
    --li-marker: "–";
    --group-fill: rgba(120, 90, 50, .035);
    --group-halo: #f2ede4;
    --group-stroke: #e3dacb;
    --group-head: transparent;
    --group-title: #1f1b16;
    --group-sub: #8a8072;
    --g-plain: #8a8072; --g-step: #46627a; --g-keep: #3d6e47; --g-human: #b8431e;
    --g-agent: #2a241d; --g-edit: #6b6152; --g-decision: #a86b12;
    --role-agent-fill: #2a241d; --role-agent-stroke: #2a241d; --role-agent-ink: #fffdf8;
    --role-human-fill: #f6e2d7; --role-human-stroke: #c0704f; --role-human-ink: #7a2e14;
    --role-keep-fill: #e4ecdf; --role-keep-stroke: #6b8f5e; --role-keep-ink: #2f5127;
    --role-reset-fill: #f4ddd5; --role-reset-stroke: #b85c4a; --role-reset-ink: #7a2a1e;
    --role-edit-fill: #efe9de; --role-edit-stroke: #b3a794; --role-edit-ink: #4a4239;
    --role-lock-fill: #ebe5da; --role-lock-stroke: #a39a8a; --role-lock-ink: #4a4239;
    --chart-1: #2a241d; --chart-2: #c9b79c; --chart-3: #e6dccb; --chart-hl: #b8431e;
    --ease-out: cubic-bezier(.22, 1, .36, 1);
    --ease-in-out: cubic-bezier(.65, 0, .35, 1);
    --ease-pop: cubic-bezier(.22, 1, .36, 1);
    --dur-1: .18s;
    --dur-2: .32s;
    --dur-3: .56s;
    --stagger: 80ms;
    --enter-x: 0px;
    --enter-y: 8px;
    --enter-scale: 1;
    --enter-rot: 0deg;
    /* aliases read by the SVG renderer and the video skin */
    --body-font: var(--font-body);
    --serif: var(--font-display);
    --ui: var(--font-body);
    --mono: var(--font-mono);
    --rule-thin: var(--line);
    --motion-draw: .7s;
    --motion-pulse: 1s;
    --motion-ease: var(--ease-out);
    --motion-silk: var(--ease-out);
    --motion-spring: var(--ease-pop);
  }
  body[data-theme="product"] {
    --bg: #f5f6f8;
    --surface: #ffffff;
    --surface-2: #f4f4f5;
    --ink: #18181b;
    --ink-2: #3f3f46;
    --muted: #71717a;
    --line: #e4e4e7;
    --line-strong: #a1a1aa;
    --accent: #4f46e5;
    --accent-ink: #ffffff;
    --accent-soft: #eef2ff;
    --ok: #059669;
    --bad: #e11d48;
    --warn: #d97706;
    --font-display: "Inter", -apple-system, "SF Pro Display", "Helvetica Neue", "PingFang SC", "Noto Sans SC", sans-serif;
    --font-body: "Inter", -apple-system, "SF Pro Text", "Helvetica Neue", "PingFang SC", "Noto Sans SC", sans-serif;
    --title-weight: 650;
    --title-align: left;
    --radius-sm: 7px;
    --radius: 10px;
    --radius-lg: 14px;
    --shadow-sm: 0 1px 2px rgba(16, 24, 40, .05);
    --shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 4px 14px -6px rgba(16, 24, 40, .10);
    --shadow-lg: 0 1px 3px rgba(16, 24, 40, .05), 0 18px 44px -18px rgba(16, 24, 40, .22);
    --node-fill: #ffffff;
    --node-stroke: #c4c4cc;
    --node-stroke-w: 1.2px;
    --node-radius: 8px;
    --label-bg: #f5f6f8;
    --bar-fill: #4f46e5;
    --li-marker: "•";
    --group-fill: #ffffff;
    --group-stroke: #e4e4e7;
    --group-head: #fafafa;
    --group-halo: #fafafa;
    --group-title: #18181b;
    --group-sub: #71717a;
    --g-plain: #71717a; --g-step: #4f46e5; --g-keep: #059669; --g-human: #e11d48;
    --g-agent: #18181b; --g-edit: #0891b2; --g-decision: #d97706;
    --role-agent-fill: #18181b; --role-agent-stroke: #18181b; --role-agent-ink: #ffffff;
    --role-human-fill: #fff1f2; --role-human-stroke: #fda4af; --role-human-ink: #9f1239;
    --role-keep-fill: #ecfdf5; --role-keep-stroke: #6ee7b7; --role-keep-ink: #065f46;
    --role-reset-fill: #fff1f2; --role-reset-stroke: #fda4af; --role-reset-ink: #9f1239;
    --role-edit-fill: #f4f4f5; --role-edit-stroke: #d4d4d8; --role-edit-ink: #3f3f46;
    --role-lock-fill: #fafafa; --role-lock-stroke: #a1a1aa; --role-lock-ink: #3f3f46;
    --chart-1: #4f46e5; --chart-2: #a5b4fc; --chart-3: #e0e7ff; --chart-hl: #f59e0b;
    --dur-1: .12s;
    --dur-2: .22s;
    --dur-3: .38s;
    --stagger: 45ms;
    --enter-x: 18px;
    --enter-y: 0px;
    --motion-draw: .5s;
  }
  /* sketch: hand drawing on parchment. Outlined boxes, ink colour = role, hand lettering. */
  @font-face {
    font-family: "Virgil";
    src: url("https://cdn.jsdelivr.net/npm/@excalidraw/excalidraw@0.18.0/dist/prod/fonts/Virgil/Virgil-Regular.woff2") format("woff2");
    font-display: swap;
  }
  body[data-theme="sketch"] {
    --bg: #f2e7cc;
    --bg-image:
      url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='260' height='260'%3E%3Cfilter id='f'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 .38 0 0 0 0 .27 0 0 0 0 .1 0 0 0 .1 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23f)'/%3E%3C/svg%3E"),
      radial-gradient(ellipse at 50% 28%, rgba(255, 249, 232, .6) 0%, rgba(255, 249, 232, 0) 62%),
      radial-gradient(ellipse at 50% 50%, rgba(120, 84, 30, 0) 55%, rgba(120, 84, 30, .18) 100%);
    --bg-size: 260px 260px, 100% 100%, 100% 100%;
    --surface: #faf3e1;
    --surface-2: #ebdfc1;
    --ink: #2a2016;
    --ink-2: #4a3c2b;
    --muted: #8b7759;
    --line: #cbb68c;
    --line-strong: #2a2016;
    --accent: #2d5a87;
    --accent-ink: #faf3e1;
    --accent-soft: #f2dd96;
    --ok: #4d7a35;
    --bad: #b0392b;
    --warn: #b8741a;
    --hand-1: #2d5a87; --hand-2: #4d7a35; --hand-3: #b8741a; --hand-4: #b0392b; --hand-5: #6b4f8a;
    --font-display: "Virgil", "LXGW WenKai", "霞鹜文楷", "Kaiti SC", STKaiti, KaiTi, cursive;
    --font-body: "Virgil", "LXGW WenKai", "霞鹜文楷", "Kaiti SC", STKaiti, KaiTi, cursive;
    --font-mono: "LXGW WenKai Mono", "SF Mono", Menlo, ui-monospace, monospace;
    --title-weight: 700;
    --title-align: left;
    --radius-sm: 6px;
    --radius: 8px;
    --radius-lg: 10px;
    --wobble: 12px 3px 10px 4px / 4px 10px 3px 12px;
    --wobble-2: 4px 12px 3px 10px / 10px 3px 12px 4px;
    --border-w: 2px;
    --shadow-sm: none;
    --shadow: none;
    --shadow-lg: none;
    --node-fill: #faf3e1;
    --node-stroke: #2a2016;
    --node-stroke-w: 2.2px;
    --node-radius: 5px;
    --label-bg: #f2e7cc;
    --bar-fill: repeating-linear-gradient(-45deg, #2d5a87 0 2px, transparent 2px 6px);
    --li-marker: "•";
    --group-fill: none;
    --group-halo: #f2e7cc;
    --group-stroke: #b9a27a;
    --group-head: transparent;
    --group-title: #2a2016;
    --group-sub: #8b7759;
    --g-plain: #2a2016; --g-step: #2d5a87; --g-keep: #4d7a35; --g-human: #b8741a;
    --g-agent: #2d5a87; --g-edit: #2f7a7a; --g-decision: #b8741a;
    --role-agent-fill: #faf3e1; --role-agent-stroke: #2d5a87; --role-agent-ink: #2a2016;
    --role-human-fill: #faf3e1; --role-human-stroke: #b8741a; --role-human-ink: #2a2016;
    --role-keep-fill: #faf3e1; --role-keep-stroke: #4d7a35; --role-keep-ink: #2a2016;
    --role-reset-fill: #faf3e1; --role-reset-stroke: #b0392b; --role-reset-ink: #2a2016;
    --role-edit-fill: #faf3e1; --role-edit-stroke: #2f7a7a; --role-edit-ink: #2a2016;
    --role-lock-fill: #faf3e1; --role-lock-stroke: #8b7759; --role-lock-ink: #2a2016;
    --chart-1: #2d5a87; --chart-2: #b8741a; --chart-3: #4d7a35; --chart-hl: #b0392b;
    --ease-pop: cubic-bezier(.34, 1.56, .64, 1);
    --dur-1: .16s;
    --dur-2: .3s;
    --dur-3: .5s;
    --stagger: 70ms;
    --enter-x: 0px;
    --enter-y: 10px;
    --enter-scale: .98;
    --enter-rot: -.6deg;
    --motion-draw: .85s;
  }

  /* ---- base ---- */
  html { background: var(--bg); color-scheme: light; }
  body {
    margin: 0;
    min-height: 100vh;
    background-color: var(--bg);
    background-image: var(--bg-image);
    background-size: var(--bg-size);
    background-attachment: local;
    color: var(--ink);
    font-family: var(--font-body);
    font-size: 16px;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
  }
  ::selection { background: var(--accent-soft); color: var(--ink); }
  .frame {
    width: min(46rem, 94vw);
    margin: 0 auto;
    padding: 3rem 1.25rem 3.5rem;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
  }
  .prose-before { display: block; order: 0; }
  .prose-before > h1 {
    margin: 0;
    font-family: var(--font-display);
    font-size: clamp(1.8rem, 3.2vw, 2.45rem);
    font-weight: var(--title-weight);
    line-height: 1.2;
    letter-spacing: -.012em;
    text-align: var(--title-align);
    text-wrap: balance;
    color: var(--ink);
  }
  .cv-subtitle,
  .prose-before > p {
    margin: .65rem 0 0;
    color: var(--ink-2);
    font-size: 1.02rem;
    line-height: 1.6;
    text-align: var(--title-align);
  }
  .prose-before > h2, .prose-after > h2 { margin: 1.8rem 0 .4rem; font-family: var(--font-display); font-size: 1.15rem; font-weight: 600; }
  .prose-before > h3, .prose-after > h3 { margin: 1.2rem 0 .3rem; font-size: 1rem; font-weight: 600; color: var(--ink-2); }
  .prose-before > ul, .prose-before > ol { margin: .8rem 0 0; color: var(--ink-2); }
  .prose-after { order: 9; margin-top: 1.75rem; color: var(--muted); font-size: .9rem; line-height: 1.65; }
  .prose-after > * { margin: .5rem 0 0; }
  .prose-after > :first-child { margin-top: 0; }
  .prose-before code, .prose-after code { font-family: var(--font-mono); font-size: .88em; background: var(--surface-2); padding: .08em .35em; border-radius: var(--radius-sm); }
  body[data-theme="book"] .prose-before::after {
    content: ""; display: block; width: 2.4rem; height: 2px; margin: 1.35rem auto 0; background: var(--accent);
  }
  .cv-rules, table.rules { display: none !important; }

  /* ---- stage ---- */
  #stage { order: 2; display: flex; justify-content: center; margin: 2rem 0 0; min-height: 4rem; }
  #stage.empty { display: none; }
  #stage.wide, #stage.sections { width: 100%; }
  #diagram { width: 100%; }
  #diagram svg { display: block; width: 100%; height: auto; margin: 0 auto; overflow: visible; background: transparent; }
  #diagram svg.cv-lanes { max-width: 100%; }
  #diagram .background, #diagram rect.background { fill: transparent !important; }
  body[data-mode="video"] #stage.sections, body[data-mode="video"] #stage.wide { width: auto; align-self: stretch; }

  /* ---- toolbar: one sticky strip on top; controls left, the step in the middle, views + themes right ---- */
  .cv-toolbar {
    position: sticky;
    top: 0;
    z-index: 40;
    display: flex;
    align-items: flex-start;
    flex-wrap: wrap;
    column-gap: 1rem;
    row-gap: .4rem;
    min-height: 3.25rem;
    padding: .45rem clamp(.75rem, 2.2vw, 1.75rem);
    box-sizing: border-box;
    background: color-mix(in srgb, var(--bg) 86%, transparent);
    -webkit-backdrop-filter: saturate(1.4) blur(12px);
    backdrop-filter: saturate(1.4) blur(12px);
    border-bottom: 1px solid var(--line);
  }
  body[data-mode="video"] .cv-toolbar { display: none; }
  .cv-tb-nav { display: flex; align-items: center; gap: .3rem; flex: 0 0 auto; }
  .cv-tb-right { display: flex; align-items: center; gap: .6rem; flex: 0 0 auto; margin-left: auto; }
  .cv-toolbar .caption-box {
    position: relative;
    flex: 1 1 18rem;
    min-width: 0;
    padding-top: .32rem;
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    column-gap: .8rem;
    row-gap: .3rem;
  }
  .caption-box .counter {
    flex: 0 0 auto;
    font-family: var(--font-display);
    font-variant-numeric: tabular-nums lining-nums;
    font-size: .92rem;
    color: var(--muted);
    white-space: nowrap;
  }
  .caption-box .counter b { color: var(--ink); font-weight: 600; }
  .cap-wrap { flex: 1 1 12rem; min-width: 0; }
  /* the bar keeps one height while stepping: one caption line, plus a reserved sub line on sheets */
  .caption-box .cap {
    display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden;
    font-size: .95rem; line-height: 1.45; color: var(--ink);
  }
  .caption-box .cap-sub { display: block; margin-top: .05rem; font-size: .78rem; line-height: 1.4; color: var(--muted); }
  .caption-box .cap-sub:empty { display: none; }
  body[data-renderer="boards"]:not([data-display="lesson"]) .cv-toolbar .cap-sub { min-height: 1.4em; }
  body[data-renderer="boards"]:not([data-display="lesson"]) .cv-toolbar .cap-sub:empty { display: block; visibility: hidden; }
  /* message bodies (detail) float under the bar instead of pushing the page down */
  .cv-toolbar .caption-box pre.detail {
    position: absolute; top: calc(100% + .55rem); left: 0; z-index: 41;
    width: min(46rem, 100%); box-sizing: border-box;
    background: var(--surface); border: 1px solid var(--line); border-left: 2px solid var(--accent);
    box-shadow: 0 14px 34px -14px rgba(0, 0, 0, .28);
  }
  .caption-box pre.detail {
    max-height: 12em;
    overflow: auto;
    margin: 0;
    padding: .5rem .75rem;
    font-family: var(--font-mono);
    font-size: .78rem;
    line-height: 1.5;
    white-space: pre-wrap;
    color: var(--ink-2);
    background: var(--surface-2);
    border-radius: var(--radius);
    border-left: 2px solid var(--accent);
  }
  .caption-box pre.detail[hidden] { display: none !important; }
  .caption-box .cap.cv-in { animation: cv-rise var(--dur-2) var(--ease-out) both; }
  .caption-box pre.detail.cv-in { animation: cv-fade var(--dur-2) var(--ease-out) both; }
  .cv-progress { position: absolute; left: 0; right: 0; bottom: -1px; height: 2px; pointer-events: none; }
  .cv-progress i { display: block; height: 100%; width: 0; background: var(--accent); transition: width var(--dur-3) var(--ease-out); }
  /* video keeps the old caption under the picture */
  body[data-mode="video"] .caption-box { display: block; order: 3; }
  body[data-mode="video"] .cap-sub { display: none !important; }
  body[data-mode="video"] .caption-box .cap { display: block; -webkit-line-clamp: none; font-size: inherit; line-height: inherit; }
  body[data-mode="video"] .caption-box .counter { font-family: var(--ui); }
  body[data-mode="video"] .caption-box pre.detail { position: static; width: auto; box-shadow: none; }
  body[data-mode="video"] .cv-group-plate { fill: #191919; }

  /* ---- buttons + pills ---- */
  button {
    font: inherit;
    font-size: .86rem;
    font-weight: 500;
    line-height: 1.2;
    color: var(--ink);
    background: var(--surface);
    border: var(--border-w) solid var(--line);
    border-radius: var(--radius-sm);
    padding: .42rem .75rem;
    cursor: pointer;
    box-shadow: var(--shadow-sm);
    transition: background var(--dur-1) var(--ease-out), color var(--dur-1) var(--ease-out),
                border-color var(--dur-1) var(--ease-out), transform var(--dur-1) var(--ease-out),
                box-shadow var(--dur-1) var(--ease-out);
  }
  button:hover:not(:disabled) { border-color: var(--line-strong); }
  button:active:not(:disabled) { transform: translateY(1px); }
  button:focus-visible, .cv-pills a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  button:disabled { opacity: .35; cursor: default; }
  #prev, #reset { min-width: 2.1rem; padding-left: .55rem; padding-right: .55rem; font-size: 1rem; line-height: 1.05; }
  button#next { background: var(--ink); color: var(--surface); border-color: var(--ink); font-weight: 600; }
  button#next:hover:not(:disabled) { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
  button#play[aria-pressed="true"] { color: var(--accent); border-color: var(--accent); }
  body[data-theme="book"] button { background-color: transparent; }
  body[data-theme="book"] button#next { background: var(--ink); }
  .cv-pills {
    display: inline-flex; align-items: center; gap: 2px; padding: 2px;
    background: var(--surface-2); border: 1px solid var(--line); border-radius: 999px;
  }
  .cv-pills[hidden] { display: none; }
  .cv-pills > button, .cv-pills > a {
    padding: .3rem .72rem; border: none; border-radius: 999px; box-shadow: none;
    background: transparent; color: var(--muted); font-size: .8rem; font-weight: 500; line-height: 1.2;
    text-decoration: none; white-space: nowrap; cursor: pointer;
    transition: background var(--dur-1) var(--ease-out), color var(--dur-1) var(--ease-out), box-shadow var(--dur-1) var(--ease-out);
  }
  .cv-pills > button:hover, .cv-pills > a:hover { color: var(--ink); border-color: transparent; }
  .cv-pills > [aria-pressed="true"], .cv-pills > [aria-current="page"] {
    background: var(--surface); color: var(--ink); box-shadow: 0 1px 2px rgba(0, 0, 0, .08), 0 0 0 1px var(--line);
  }
  body[data-theme="book"] .cv-pills { border-radius: 3px; background: transparent; }
  body[data-theme="book"] .cv-pills > * { border-radius: 2px; }
  body[data-theme="book"] .cv-pills > [aria-pressed="true"], body[data-theme="book"] .cv-pills > [aria-current="page"] {
    background: var(--ink); color: var(--surface); box-shadow: none;
  }
  @media (max-width: 900px) {
    .cv-toolbar .caption-box { order: 3; flex-basis: 100%; }
    .caption-box .cap { -webkit-line-clamp: 2; }
  }

  /* =====================================================================
     Diagrams: own lane/section SVG (layout.py) and mermaid
     ===================================================================== */
  #diagram g.node rect, #diagram g.node polygon, #diagram g.node circle, #diagram g.node ellipse,
  #diagram g.node .label-container {
    stroke-width: var(--node-stroke-w);
    transition: stroke var(--dur-2) var(--ease-out), stroke-width var(--dur-2) var(--ease-out), fill var(--dur-2) var(--ease-out);
  }
  #diagram g.node rect.label-container { rx: var(--node-radius); ry: var(--node-radius); }
  #diagram g.node text, #diagram .nodeLabel, #diagram .nodeLabel *,
  #diagram .label foreignObject div, #diagram .label foreignObject span, #diagram .label foreignObject p {
    font-family: var(--font-body) !important;
    background: transparent !important;
  }
  #diagram .nodeLabel p { margin: 0; line-height: 1.4; }
  #diagram .nodeLabel code, #diagram .label foreignObject code { font-family: var(--font-mono) !important; font-size: .85em !important; }
  .cv-node-title { font-family: var(--font-body); font-weight: 600; font-size: 15px; }
  .cv-node-sub { font-family: var(--font-body); font-size: 12.5px; opacity: .78; }
  #diagram .edgePath path, #diagram path.flowchart-link, #diagram .fallback-edge, .cv-link {
    stroke: var(--ink-2);
    stroke-width: 1.2px;
    fill: none;
  }
  #diagram path.flowchart-link, #diagram .edgePath path { stroke: var(--ink-2) !important; stroke-width: 1.2px !important; }
  .cv-arrowhead, #diagram marker path, #diagram .arrowMarkerPath { fill: var(--ink-2) !important; stroke: none !important; }
  #diagram .edgeLabel, #diagram .edgeLabel foreignObject div, #diagram .edgeLabel foreignObject span, #diagram .edgeLabel foreignObject p {
    font-family: var(--font-body) !important;
    font-size: 12.5px !important;
    color: var(--ink-2) !important;
    background: var(--label-bg) !important;
  }
  .cv-label-bg { fill: var(--label-bg); stroke: var(--line); stroke-width: 1px; }
  .cv-edge-text { font-family: var(--font-body); font-size: 12.5px; fill: var(--ink-2); }
  .cv-step-badge { fill: var(--ink-2); stroke: none; transition: fill var(--dur-2) var(--ease-out); }
  .cv-step-num { font-family: var(--font-body); font-size: 11px; font-weight: 700; fill: var(--surface); }

  /* step states */
  #diagram g.node { transition: opacity var(--dur-3) var(--ease-out); }
  #diagram g.node.cv-dim { opacity: .28; }
  #diagram g.node.cv-lit { opacity: 1; }
  #diagram g.node.cv-current { cursor: pointer; }
  #diagram g.node.cv-current rect, #diagram g.node.cv-current polygon, #diagram g.node.cv-current circle,
  #diagram g.node.cv-current .label-container,
  #diagram g.rough-node.cv-current .label-container path:last-child {
    stroke: var(--accent) !important;
    stroke-width: 2.2px !important;
  }
  /* never animate `transform` on mermaid groups: it would override their translate() attribute */
  #diagram g.node.cv-current rect, #diagram g.node.cv-current polygon, #diagram g.node.cv-current .label-container {
    animation: cv-stroke-in var(--dur-3) var(--ease-out) both;
  }
  #diagram .cv-edge { transition: opacity var(--dur-2) var(--ease-out), stroke var(--dur-2) var(--ease-out); }
  #diagram .cv-edge.cv-future { opacity: .16; }
  #diagram .cv-edge.cv-next { opacity: .5; stroke-dasharray: 3 4; }
  #diagram .cv-edge.cv-done { opacity: 1; }
  #diagram .cv-edge.cv-cur, #diagram .cv-edge.cv-flow { stroke: var(--accent) !important; stroke-width: 2.2px !important; opacity: 1; }
  #diagram .cv-edge.cv-flow {
    stroke-dasharray: 1;
    stroke-dashoffset: 1;
    animation: cv-edge-draw var(--motion-draw) var(--ease-in-out) forwards;
  }
  #diagram .cv-edge.cv-loop.is-on, #diagram .cv-link.cv-loop.is-on { stroke: var(--accent); }
  #diagram .cv-hit { fill: none; stroke: transparent; stroke-width: 16px; pointer-events: stroke; }
  #diagram .cv-hit.cv-next { cursor: pointer; }
  #diagram .cv-hit.cv-next:hover { stroke: var(--accent); stroke-opacity: .12; }
  #diagram .edgeLabel { transition: opacity var(--dur-2) var(--ease-out); }
  #diagram .edgeLabel.cv-future { opacity: .2; }
  #diagram .edgeLabel.cv-next { opacity: .6; cursor: pointer; }
  #diagram .edgeLabel.cv-done { opacity: 1; }
  #diagram .edgeLabel.cv-flow .cv-step-badge, #diagram .edgeLabel.cv-cur .cv-step-badge { fill: var(--accent); }
  #diagram .edgeLabel.cv-flow .cv-edge-text, #diagram .edgeLabel.cv-cur .cv-edge-text { fill: var(--accent); font-weight: 600; }
  #diagram .edgeLabel.cv-flow .cv-label-bg, #diagram .edgeLabel.cv-cur .cv-label-bg { stroke: var(--accent); }
  #diagram .edgeLabel.cv-flow .cv-step-badge { animation: cv-badge-pop var(--dur-3) var(--ease-pop) both; transform-box: fill-box; transform-origin: center; }
  #cv-packet { fill: var(--accent) !important; pointer-events: none; }

  /* regions: lane / section groups */
  .cv-group { --gc: var(--g-plain); transition: opacity var(--dur-3) var(--ease-out); }
  .cv-group.grole-step { --gc: var(--g-step); }
  .cv-group.grole-keep { --gc: var(--g-keep); }
  .cv-group.grole-human, .cv-group.grole-reset { --gc: var(--g-human); }
  .cv-group.grole-agent { --gc: var(--g-agent); }
  .cv-group.grole-editable { --gc: var(--g-edit); }
  .cv-group.grole-decision { --gc: var(--g-decision); }
  .cv-group-fill { fill: var(--group-fill); stroke: none; rx: var(--radius); ry: var(--radius); }
  .cv-group-box { fill: none; stroke: var(--group-stroke); stroke-width: 1px; rx: var(--radius); ry: var(--radius);
                  transition: stroke var(--dur-2) var(--ease-out), stroke-width var(--dur-2) var(--ease-out); }
  .cv-group-plate { fill: var(--group-halo); stroke: none; }
  .cv-group-headbg { fill: var(--group-head); stroke: none; }
  .cv-group-under .cv-group-headbg { rx: var(--radius); ry: var(--radius); }
  .cv-group-badge { fill: var(--gc); stroke: none; }
  rect.cv-group-badge { rx: 5px; ry: 5px; }
  .cv-group-letter { font-family: var(--font-body); font-size: 13px; font-weight: 700; fill: #fff; }
  .cv-group-title { font-family: var(--font-display); font-size: 15px; font-weight: 600; fill: var(--group-title); }
  .cv-group-sub { font-family: var(--font-body); font-size: 12px; fill: var(--group-sub); }
  .cv-group.cv-group-on .cv-group-box { stroke: var(--gc); stroke-width: 1.6px; }
  .cv-group.cv-group-on .cv-group-badge { animation: cv-badge-pop var(--dur-3) var(--ease-pop) both; transform-box: fill-box; transform-origin: center; }
  #diagram .cluster rect { fill: var(--group-fill) !important; stroke: var(--group-stroke) !important; stroke-width: 1px !important; rx: var(--radius); ry: var(--radius); }
  #diagram .cluster-label, #diagram .cluster-label * { font-family: var(--font-body) !important; font-weight: 600; color: var(--group-title) !important; fill: var(--group-title) !important; }
  body[data-theme="book"] .cv-group-box { stroke-dasharray: none; }
  body[data-theme="book"] rect.cv-group-badge { rx: 2px; ry: 2px; }
  body[data-theme="product"] .cv-group-fill { filter: drop-shadow(0 1px 2px rgba(16, 24, 40, .07)); }
  body[data-theme="product"] .cv-label-bg { fill: #ffffff; }

  /* page regions (chart page: chart / notes) */
  .cv-region {
    position: relative; order: 2; margin: 1.6rem 0 0; padding: 1rem 1.25rem 1.25rem;
    background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-lg); box-shadow: var(--shadow);
  }
  .cv-region-head { display: flex; align-items: center; gap: .55rem; margin: 0 0 .9rem; }
  span.cv-group-badge {
    display: inline-flex; align-items: center; justify-content: center;
    min-width: 1.45rem; height: 1.45rem; padding: 0 .3rem; box-sizing: border-box;
    border-radius: 6px; background: var(--gc); color: #fff;
    font-family: var(--font-body); font-size: .75rem; font-weight: 700;
  }
  .cv-region-title { font-family: var(--font-display); font-weight: 600; font-size: 1rem; }
  .cv-region-sub { color: var(--muted); font-size: .85rem; }
  .cv-region .caption-box { margin-top: 0; min-height: 0; }
  .cv-region .cv-chart { margin-top: 0; }
  .cv-region .prose-after { margin-top: .9rem; }
  body[data-theme="book"] .cv-region { box-shadow: none; }

  /* ---- chart block ---- */
  .cv-chart { order: 2; margin: 1.6rem 0 0; }
  .cv-chart-title { text-align: center; font-family: var(--font-display); font-weight: 600; font-size: 1.05rem; }
  .cv-sample {
    display: inline-block; margin-left: .55rem; padding: .05rem .5rem; vertical-align: 2px;
    font-family: var(--font-body); font-size: .72rem; font-weight: 500; color: var(--muted);
    border: 1px solid var(--line); border-radius: 99px;
  }
  .cv-legend { text-align: center; font-size: .85rem; color: var(--ink-2); margin: .4rem 0 .2rem; }
  .cv-legend-item { margin: 0 .65rem; white-space: nowrap; }
  .cv-swatch { display: inline-block; width: .75rem; height: .75rem; margin-right: .35rem; vertical-align: -1px; border-radius: 2px; }
  .cv-swatch.cv-s0 { background: var(--chart-1); } .cv-swatch.cv-s1 { background: var(--chart-2); } .cv-swatch.cv-s2 { background: var(--chart-3); }
  .cv-chart-svg { width: 100%; height: auto; display: block; overflow: visible; }
  .cv-grid { stroke: var(--line); stroke-width: 1px; }
  .cv-axis { stroke: var(--line-strong); stroke-width: 1.2px; }
  .cv-tick, .cv-cat, .cv-axis-label { font-family: var(--font-body); font-size: 12.5px; fill: var(--muted); }
  .cv-cat { fill: var(--ink-2); transition: opacity var(--dur-2) var(--ease-out); }
  .cv-cat.cv-hidden { opacity: .25; }
  .cv-cat.cv-hl { font-weight: 700; fill: var(--chart-hl); }
  .cv-bar {
    stroke: none;
    transition: opacity var(--dur-2) var(--ease-out), transform var(--dur-3) var(--ease-out), stroke var(--dur-1);
  }
  .cv-bar.cv-s0 { fill: var(--chart-1); } .cv-bar.cv-s1 { fill: var(--chart-2); } .cv-bar.cv-s2 { fill: var(--chart-3); }
  .cv-bar.cv-hidden { transform: scaleY(0); opacity: 0; }
  .cv-seg { fill: none; stroke-width: 2.2px; transition: stroke-dashoffset var(--dur-3) var(--ease-out), opacity var(--dur-2); }
  .cv-seg.cv-s0 { stroke: var(--chart-1); } .cv-seg.cv-s1 { stroke: var(--chart-2); } .cv-seg.cv-s2 { stroke: var(--chart-3); }
  .cv-seg.cv-hidden { stroke-dashoffset: var(--len); opacity: 0; }
  .cv-dot { stroke: var(--surface); stroke-width: 1.5px; transition: opacity var(--dur-2) var(--ease-out); }
  .cv-dot.cv-s0 { fill: var(--chart-1); } .cv-dot.cv-s1 { fill: var(--chart-2); } .cv-dot.cv-s2 { fill: var(--chart-3); }
  .cv-dot.cv-hidden { opacity: 0; }
  .cv-mark.cv-faded { opacity: .3; }
  .cv-bar.cv-hl { stroke: var(--chart-hl); stroke-width: 2.4px; }
  .cv-dot.cv-hl { r: 7.5; stroke: var(--chart-hl); stroke-width: 2.4px; }
  .cv-hitcol { fill: transparent; stroke: none !important; cursor: pointer; }
  .cv-readout { text-align: center; font-size: .9rem; min-height: 1.5em; margin-top: .35rem; color: var(--muted); }
  .cv-readout b { color: var(--ink); font-weight: 600; }
  .cv-chart-source { text-align: center; font-size: .78rem; color: var(--muted); margin-top: .2rem; }

  /* =====================================================================
     Teaching boards: sheet (all at once) · deck (one lights up) · lesson
     ===================================================================== */
  body[data-renderer="boards"] .frame { width: min(76rem, 96vw); }
  body[data-renderer="boards"][data-display="sheet"] .frame,
  body[data-renderer="boards"][data-display="deck"] .frame { width: min(96rem, 97vw); }
  body[data-renderer="boards"] #stage.sections { width: 100%; margin-top: 1.5rem; }
  body[data-orientation="portrait"][data-renderer="boards"] .frame { width: min(48rem, 96vw); }
  body[data-renderer="boards"][data-display="sheet"] .prose-before > h1,
  body[data-renderer="boards"][data-display="deck"] .prose-before > h1 { font-size: clamp(1.5rem, 2.4vw, 1.95rem); }
  .cv-sheet { width: 100%; }
  .cv-boards {
    display: grid;
    grid-template-columns: repeat(var(--cv-cols, 3), minmax(0, 1fr));
    grid-auto-rows: auto;
    gap: .75rem;
    width: 100%;
    align-items: stretch;
  }
  body[data-orientation="portrait"] .cv-boards { grid-template-columns: 1fr !important; }
  body[data-orientation="portrait"] .cv-board { grid-column: 1 / -1 !important; }
  @media (max-width: 760px) {
    .cv-boards { grid-template-columns: 1fr !important; }
    .cv-board { grid-column: 1 / -1 !important; }
  }
  .cv-board {
    position: relative;
    display: flex;
    flex-direction: column;
    min-width: 0;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow);
    transition: border-color var(--dur-2) var(--ease-out), box-shadow var(--dur-2) var(--ease-out),
                opacity var(--dur-3) var(--ease-out), transform var(--dur-3) var(--ease-out);
  }
  .cv-board-head {
    display: flex; align-items: center; gap: .55rem;
    padding: .7rem .9rem .25rem;
    cursor: pointer;
  }
  .cv-board-badge, span.cv-board-badge {
    flex: 0 0 auto;
    min-width: 1.4rem; height: 1.4rem;
    background: var(--ink); color: var(--surface);
    font-family: var(--font-body); font-size: .74rem; font-weight: 700;
    transition: background var(--dur-2) var(--ease-out), color var(--dur-2) var(--ease-out);
  }
  .cv-board-title {
    flex: 0 1 auto; min-width: 0;
    font-family: var(--font-display); font-size: .98rem; font-weight: 600; line-height: 1.35; color: var(--ink);
  }
  .cv-board-sub {
    margin-left: auto; padding-left: .5rem;
    font-family: var(--font-mono); font-size: .7rem; color: var(--muted); white-space: nowrap; letter-spacing: .01em;
  }
  .cv-board-zoom {
    flex: 0 0 auto; margin-left: .25rem; padding: .1rem .38rem; min-width: 0;
    font-size: .85rem; line-height: 1; color: var(--muted); background: transparent; border-color: transparent; box-shadow: none;
    opacity: 0; transition: opacity var(--dur-1) var(--ease-out), color var(--dur-1) var(--ease-out), border-color var(--dur-1) var(--ease-out);
  }
  .cv-board-sub + .cv-board-zoom { margin-left: .25rem; }
  .cv-board-title + .cv-board-zoom { margin-left: auto; }
  .cv-board:hover .cv-board-zoom, .cv-board-zoom:focus-visible { opacity: 1; }
  @media (hover: none) { .cv-board-zoom { opacity: .6; } }
  .cv-board-zoom:hover:not(:disabled) { color: var(--ink); border-color: var(--line); }
  body[data-display="lesson"] .cv-board-zoom { display: none; }
  .cv-board-body {
    flex: 1;
    display: flex; flex-direction: column; gap: .7rem;
    padding: .45rem .9rem .85rem;
    font-size: .88rem;
    color: var(--ink);
    min-width: 0;
  }
  .cv-board.cv-board-on { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 14%, transparent), var(--shadow); }
  .cv-board.cv-board-on .cv-board-badge { background: var(--accent); color: var(--accent-ink); }
  body[data-display="deck"] .cv-board.cv-dim { opacity: .3; }
  body[data-display="deck"] .cv-board.cv-board-enter { animation: cv-enter var(--dur-3) var(--ease-out) both; }
  body[data-display="sheet"] .cv-board.cv-board-enter .cv-board-badge { animation: cv-badge-pop var(--dur-3) var(--ease-pop) both; }
  body[data-theme="book"] .cv-board { box-shadow: none; border-color: var(--line-strong); border-radius: 0; }
  body[data-theme="book"] .cv-board.cv-board-on { border-color: var(--line-strong); box-shadow: inset 0 3px 0 var(--accent); }
  body[data-theme="book"] span.cv-board-badge { border-radius: 1px; }

  /* ---- drawing sheet: double frame, coordinate rulers on four sides, title strip at the bottom ---- */
  .cv-drawing {
    --rule: 1.5rem;
    position: relative;
    box-sizing: border-box;
    padding: calc(var(--rule) + .85rem);
    border: 1px solid var(--line-strong);
    background: var(--surface);
  }
  .cv-drawing::before {
    content: ""; position: absolute; inset: var(--rule); pointer-events: none;
    border: 1px solid var(--line-strong);
  }
  #stage.cv-drawing { display: block; }
  .cv-ruler {
    position: absolute; display: grid; pointer-events: none;
    font-family: var(--font-mono); font-size: .62rem; color: var(--muted);
  }
  .cv-ruler span { display: flex; align-items: center; justify-content: center; }
  .cv-ruler-top, .cv-ruler-bottom { left: var(--rule); right: var(--rule); height: var(--rule); grid-template-columns: repeat(8, 1fr); }
  .cv-ruler-top { top: 0; }
  .cv-ruler-bottom { bottom: 0; }
  .cv-ruler-left, .cv-ruler-right { top: var(--rule); bottom: var(--rule); width: var(--rule); grid-template-rows: repeat(4, 1fr); }
  .cv-ruler-left { left: 0; }
  .cv-ruler-right { right: 0; }
  .cv-ruler-top span + span, .cv-ruler-bottom span + span { border-left: 1px solid var(--line-strong); }
  .cv-ruler-left span + span, .cv-ruler-right span + span { border-top: 1px solid var(--line-strong); }
  .cv-titleblock {
    display: flex; flex-wrap: wrap; margin-top: .75rem;
    border: 1px solid var(--line-strong);
    font-size: .8rem;
  }
  .cv-tb-cell { flex: 1 1 8rem; min-width: 0; padding: .35rem .65rem .4rem; border-left: 1px solid var(--line); }
  .cv-tb-cell:first-child { border-left: none; }
  .cv-tb-cell.cv-tb-title { flex: 3 1 16rem; }
  .cv-tb-cell span { display: block; font-family: var(--font-mono); font-size: .62rem; color: var(--muted); letter-spacing: .02em; }
  .cv-tb-cell b { display: block; margin-top: .1rem; font-weight: 600; color: var(--ink); overflow-wrap: anywhere; }
  .cv-tb-cell.cv-tb-title b { font-family: var(--font-display); font-size: 1rem; }
  body[data-theme="product"] .cv-drawing { border-color: var(--line); border-radius: var(--radius-lg); box-shadow: var(--shadow); }
  body[data-theme="product"] .cv-drawing::before { border-color: var(--line); border-radius: var(--radius); }
  body[data-theme="product"] .cv-ruler span + span { border-color: var(--line) !important; }
  body[data-theme="product"] .cv-titleblock { border-color: var(--line); border-radius: var(--radius); background: var(--surface-2); }
  body[data-theme="sketch"] .cv-drawing { background: transparent; border: 2px solid var(--ink); border-radius: var(--wobble-2); }
  body[data-theme="sketch"] .cv-drawing::before { border: 1.5px dashed var(--line); border-radius: var(--wobble); }
  body[data-theme="sketch"] .cv-ruler { font-family: var(--font-body); font-size: .78rem; color: var(--ink-2); }
  body[data-theme="sketch"] .cv-ruler span + span { border-color: var(--line) !important; }
  body[data-theme="sketch"] .cv-titleblock { border: 2px solid var(--ink); border-radius: var(--wobble); }
  body[data-theme="sketch"] .cv-tb-cell span { font-family: var(--font-body); font-size: .74rem; }
  body[data-theme="sketch"] .cv-tb-cell b { font-weight: 400; font-size: .95rem; }

  /* narrow screens: drop the drawing chrome, let wide diagrams scroll instead of shrinking to nothing */
  @media (max-width: 760px) {
    .cv-ruler, .cv-titleblock { display: none; }
    .cv-drawing { --rule: 0px; padding: .75rem; }
    .cv-drawing::before { display: none; }
    #stage.cv-drawing { overflow-x: auto; }
    #stage.cv-drawing #diagram svg.cv-lanes { min-width: 720px; }
  }

  /* ---- sheet density: one landscape screen, STE-drawing type sizes ---- */
  body[data-display="sheet"] .cv-board-body, body[data-display="deck"] .cv-board-body { font-size: .82rem; gap: .6rem; }
  body[data-display="sheet"] .cv-callout p, body[data-display="deck"] .cv-callout p { font-size: .86rem; line-height: 1.55; }
  body[data-display="sheet"] .cv-table, body[data-display="deck"] .cv-table { font-size: .8rem; }
  body[data-display="sheet"] .cv-table th, body[data-display="deck"] .cv-table th { padding: .3rem .5rem; font-size: .7rem; font-family: var(--font-mono); font-weight: 500; }
  body[data-display="sheet"] .cv-table td, body[data-display="deck"] .cv-table td { padding: .32rem .5rem; line-height: 1.4; }
  body[data-display="sheet"] .cv-board-list, body[data-display="deck"] .cv-board-list { font-size: .82rem; line-height: 1.5; }
  body[data-display="sheet"] .cv-li, body[data-display="deck"] .cv-li { padding-top: .12rem; padding-bottom: .12rem; }
  body[data-display="sheet"] .cv-tok, body[data-display="deck"] .cv-tok { min-width: 3.4rem; padding: .4rem .6rem .35rem; font-size: 1rem; }
  body[data-display="sheet"] .cv-code, body[data-display="deck"] .cv-code { font-size: .76rem; padding: .55rem .7rem; }

  /* ---- gauges (bars) on the sheet: label + limit, a thin track, the needle, and a tick scale ---- */
  body[data-display="sheet"] .cv-bars, body[data-display="deck"] .cv-bars { gap: .55rem; }
  body[data-display="sheet"] .cv-bar-row, body[data-display="deck"] .cv-bar-row {
    grid-template-columns: minmax(0, 1fr) max-content; row-gap: .2rem; padding: .15rem .35rem .1rem;
  }
  body[data-display="sheet"] .cv-bar-label, body[data-display="deck"] .cv-bar-label { grid-column: 1; grid-row: 1; font-size: .8rem; }
  body[data-display="sheet"] .cv-bar-val, body[data-display="deck"] .cv-bar-val {
    grid-column: 2; grid-row: 1; font-family: var(--font-mono); font-size: .72rem; color: var(--accent);
  }
  body[data-display="sheet"] .cv-bar-track, body[data-display="deck"] .cv-bar-track {
    grid-column: 1 / -1; grid-row: 2; height: 7px; overflow: visible; border-radius: 1px;
    background: color-mix(in srgb, var(--accent) 9%, var(--surface)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 28%, var(--line));
  }
  body[data-display="sheet"] .cv-bar-fill, body[data-display="deck"] .cv-bar-fill {
    background: color-mix(in srgb, var(--accent) 22%, var(--surface)); border-radius: 1px;
  }
  body[data-display="sheet"] .cv-bar-needle, body[data-display="deck"] .cv-bar-needle {
    display: block; position: absolute; top: -4px; bottom: -4px; width: 2px; margin-left: -1px;
    background: var(--ink); transition: left calc(var(--dur-3) * 1.6) var(--ease-out);
  }
  body[data-display="sheet"] .cv-bar-row.cv-bar-focus .cv-bar-needle, body[data-display="deck"] .cv-bar-row.cv-bar-focus .cv-bar-needle { background: var(--accent); }
  body[data-display="sheet"] .cv-bar-ticks, body[data-display="deck"] .cv-bar-ticks {
    display: block; position: relative; grid-column: 1 / -1; grid-row: 3; height: .9rem;
    font-family: var(--font-mono); font-size: .58rem; color: var(--muted);
  }
  .cv-bar-ticks span { position: absolute; top: 0; transform: translateX(-50%); white-space: nowrap; }
  .cv-bar-ticks span::before { content: ""; position: absolute; left: 50%; top: -.38rem; width: 1px; height: .3rem; background: var(--line-strong); opacity: .5; }
  .cv-bar-ticks span:first-child { transform: none; }
  .cv-bar-ticks span:first-child::before { left: 0; }
  .cv-bar-ticks span:last-child:not(:first-child) { transform: translateX(-100%); }
  .cv-bar-ticks span:last-child:not(:first-child)::before { left: auto; right: 0; }
  body[data-theme="sketch"][data-display="sheet"] .cv-bar-track, body[data-theme="sketch"][data-display="deck"] .cv-bar-track {
    height: 12px; background: transparent; box-shadow: none;
  }
  body[data-theme="sketch"][data-display="sheet"] .cv-bar-fill, body[data-theme="sketch"][data-display="deck"] .cv-bar-fill { background: var(--bar-fill); }
  body[data-theme="sketch"] .cv-bar-ticks, body[data-theme="sketch"] .cv-bar-val { font-family: var(--font-body) !important; }

  /* ---- one board opened on its own ---- */
  html.cv-zoom-open { overflow: hidden; }
  dialog.cv-zoom {
    width: min(1180px, 92vw); max-height: 88vh; padding: 0; overflow: auto; overscroll-behavior: contain;
    border: none; background: transparent; color: var(--ink);
  }
  dialog.cv-zoom::backdrop { background: rgba(20, 18, 15, .38); -webkit-backdrop-filter: blur(3px); backdrop-filter: blur(3px); }
  dialog.cv-zoom[open] { animation: cv-enter var(--dur-3) var(--ease-out) both; }
  .cv-board.cv-board-zoomed { box-shadow: var(--shadow-lg), 0 30px 80px -20px rgba(0, 0, 0, .35); }
  .cv-board-zoomed .cv-board-head { padding: 1.1rem 1.4rem .4rem; }
  .cv-board-zoomed .cv-board-title { font-size: 1.35rem; }
  .cv-board-zoomed .cv-board-sub { font-size: .85rem; }
  .cv-board-zoomed .cv-board-zoom { opacity: 1; font-size: 1.2rem; }
  .cv-board-zoomed .cv-board-body { padding: .6rem 1.4rem 1.4rem; gap: 1.1rem; font-size: 1.02rem; }
  .cv-board-zoomed .cv-callout p { font-size: 1.08rem; }
  .cv-board-zoomed .cv-table { font-size: .98rem; }
  .cv-board-zoomed .cv-md svg { max-width: none !important; max-height: 70vh; }
  body[data-theme="sketch"] .cv-board.cv-board-zoomed { background-color: var(--bg); background-image: var(--bg-image); background-size: var(--bg-size); }

  /* block: chips */
  .cv-tokens { display: flex; flex-wrap: wrap; justify-content: center; gap: .6rem; }
  .cv-tok {
    min-width: 4.2rem;
    padding: .55rem .85rem .5rem;
    text-align: center;
    font-family: var(--font-display);
    font-size: 1.15rem;
    font-weight: 600;
    line-height: 1.25;
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    transition: transform var(--dur-2) var(--ease-pop), border-color var(--dur-2) var(--ease-out),
                background var(--dur-2) var(--ease-out), color var(--dur-2) var(--ease-out),
                opacity var(--dur-2) var(--ease-out), box-shadow var(--dur-2) var(--ease-out);
  }
  .cv-tok-idx { display: block; margin-top: .2rem; font-family: var(--font-body); font-size: .72rem; font-weight: 500; color: var(--muted); }
  .cv-block .cv-tok:not(.on) { opacity: .35; }
  .cv-tok.on { border-color: var(--line-strong); }
  .cv-tok.cv-tok-focus { border-color: var(--accent); color: var(--accent); transform: translateY(-2px); }
  body[data-theme="product"] .cv-tok { box-shadow: var(--shadow-sm); }
  body[data-theme="product"] .cv-tok.cv-tok-focus { background: var(--accent-soft); }

  /* block: callout */
  .cv-callout { margin: 0; transition: background var(--dur-2) var(--ease-out), box-shadow var(--dur-2) var(--ease-out); }
  .cv-callout .cv-callout-bar { display: none; }
  .cv-callout p { margin: 0; font-size: 1rem; line-height: 1.65; color: var(--ink); text-wrap: pretty; }
  body[data-theme="book"] .cv-callout { padding: .1rem 0 .1rem 1rem; border-left: 2px solid var(--accent); }
  body[data-theme="book"] .cv-callout p { font-family: var(--font-display); font-size: 1.06rem; color: var(--ink-2); }
  body[data-theme="product"] .cv-callout { padding: .8rem 1rem; background: var(--surface-2); border-radius: var(--radius); }
  body[data-theme="product"] .cv-callout.cv-callout-on { background: var(--accent-soft); }
  .cv-callout.tone-warn { border-left-color: var(--warn); }
  .cv-callout.tone-ok { border-left-color: var(--ok); }
  .cv-callout.cv-callout-enter { animation: cv-rise var(--dur-3) var(--ease-out) both; }

  /* block: text + list */
  .cv-board-text { margin: 0; font-size: .98rem; line-height: 1.7; color: var(--ink-2); }
  .cv-board-list { list-style: none; margin: 0; padding: 0; font-size: .95rem; line-height: 1.6; }
  .cv-li {
    position: relative; margin: 0; padding: .25rem .4rem .25rem 1.35rem; border-radius: var(--radius-sm);
    transition: opacity var(--dur-2) var(--ease-out), background var(--dur-2) var(--ease-out), color var(--dur-2) var(--ease-out);
    cursor: pointer;
  }
  .cv-li::before { content: var(--li-marker); position: absolute; left: .35rem; top: .25rem; color: var(--muted); }
  .cv-li:has(.cv-sym)::before { content: none; }
  .cv-li:has(.cv-sym) { padding-left: .4rem; }
  .cv-li.cv-li-pending { opacity: .25; }
  .cv-li.cv-li-focus { background: var(--accent-soft); color: var(--ink); }
  .cv-li.cv-li-enter { animation: cv-rise var(--dur-2) var(--ease-out) both; }
  .cv-sym {
    display: inline-flex; align-items: center; justify-content: center;
    width: 1.15em; height: 1.15em; margin-right: .45em; vertical-align: -.12em;
    border-radius: 50%; font-family: var(--font-body); font-size: .78em; font-weight: 700; color: #fff;
  }
  .cv-sym.ok { background: var(--ok); }
  .cv-sym.bad { background: var(--bad); }
  .cv-sym.warn { background: var(--warn); }

  /* block: table */
  .cv-table-wrap { overflow-x: auto; }
  .cv-table {
    width: 100%;
    border-collapse: collapse;
    font-size: .9rem;
    font-variant-numeric: tabular-nums;
  }
  .cv-table th {
    padding: .5rem .7rem;
    text-align: left;
    font-size: .8rem;
    font-weight: 600;
    color: var(--muted);
    white-space: normal;
    word-break: break-word;
  }
  .cv-table td {
    padding: .55rem .7rem;
    vertical-align: top;
    line-height: 1.5;
    border-top: 1px solid var(--line);
    white-space: normal;
    word-break: break-word;
    transition: background var(--dur-2) var(--ease-out), color var(--dur-2) var(--ease-out);
  }
  .cv-table th.num, .cv-table td.num { text-align: right; }
  .cv-table code { font-family: var(--font-mono); font-size: .85em; background: var(--surface-2); padding: .05em .3em; border-radius: 3px; }
  .cv-tr { cursor: pointer; transition: opacity var(--dur-2) var(--ease-out); }
  .cv-tr.cv-tr-pending { opacity: .2; }
  .cv-tr.cv-tr-focus td { background: var(--accent-soft); color: var(--ink); }
  .cv-tr.cv-tr-focus td:first-child { box-shadow: inset 3px 0 0 var(--accent); }
  .cv-tr.cv-tr-enter { animation: cv-fade var(--dur-3) var(--ease-out) both; }
  .cv-board .cv-tr:hover td, .cv-board .cv-tr.cv-tr-hover td { background: color-mix(in srgb, var(--accent-soft) 55%, transparent); }
  body[data-theme="book"] .cv-table { border-top: 1.5px solid var(--line-strong); border-bottom: 1.5px solid var(--line-strong); }
  body[data-theme="book"] .cv-table thead th { border-bottom: 1px solid var(--line-strong); color: var(--ink-2); }
  body[data-theme="book"] .cv-table tbody tr:first-child td { border-top: none; }
  body[data-theme="product"] .cv-table-wrap { border: 1px solid var(--line); border-radius: var(--radius); }
  body[data-theme="product"] .cv-table th { background: var(--surface-2); }

  /* block: bars */
  .cv-bars { display: flex; flex-direction: column; gap: .7rem; }
  .cv-bar-row {
    display: grid;
    grid-template-columns: minmax(3.5rem, max-content) minmax(6rem, 1fr) max-content;
    align-items: center;
    column-gap: .85rem;
    padding: .2rem .35rem;
    margin: 0 -.35rem;
    border-radius: var(--radius-sm);
    cursor: pointer;
    transition: opacity var(--dur-2) var(--ease-out), background var(--dur-2) var(--ease-out);
  }
  .cv-bar-meta { display: contents; }
  .cv-bar-label { grid-column: 1; font-weight: 600; font-size: .92rem; color: var(--ink); word-break: break-word; }
  .cv-bar-track {
    grid-column: 2; grid-row: 1;
    position: relative; height: 8px; overflow: hidden;
    background: var(--surface-2); border-radius: 99px;
  }
  .cv-bar-fill {
    position: absolute; left: 0; top: 0; bottom: 0; width: 0;
    background: var(--bar-fill); border-radius: inherit;
    transition: width calc(var(--dur-3) * 1.6) var(--ease-out);
  }
  .cv-bar-val { grid-column: 3; grid-row: 1; font-size: .85rem; color: var(--muted); font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
  .cv-bar-needle, .cv-bar-ticks { display: none; }
  .cv-bar-row.cv-bar-pending { opacity: .35; }
  .cv-bar-row.cv-bar-focus { background: var(--accent-soft); }
  .cv-bar-row.cv-bar-focus .cv-bar-fill { background: var(--accent); }
  .cv-bar-note { margin: .15rem 0 0; font-size: .85rem; color: var(--muted); }
  body[data-theme="book"] .cv-bar-track { height: 6px; border-radius: 1px; background: var(--line); }
  body[data-theme="book"] .cv-bar-row.cv-bar-focus { background: transparent; }
  body[data-theme="book"] .cv-bar-row.cv-bar-focus .cv-bar-label { color: var(--accent); }

  /* block: code */
  .cv-code {
    display: block; margin: 0; padding: .75rem 1rem; overflow-x: auto;
    font-family: var(--font-mono); font-size: .82rem; line-height: 1.6; color: var(--ink);
    background: var(--surface-2); border-radius: var(--radius);
  }
  .cv-code-line {
    display: block; padding: 0 .4rem; margin: 0 -.4rem; white-space: pre; border-radius: 3px; cursor: pointer;
    transition: opacity var(--dur-2) var(--ease-out), background var(--dur-2) var(--ease-out);
  }
  .cv-code-line.cv-code-pending { opacity: .25; }
  .cv-code-line.cv-code-focus { background: color-mix(in srgb, var(--accent) 14%, transparent); box-shadow: inset 2px 0 0 var(--accent); }
  .cv-code-line.cv-code-enter { animation: cv-fade var(--dur-2) var(--ease-out) both; }

  /* block: mermaid (flow / seq) */
  .cv-md { position: relative; margin: 0; overflow: visible; }
  .cv-md-src { display: none; }
  .cv-md svg { display: block; width: 100%; height: auto; margin: 0 auto; overflow: visible; animation: cv-fade var(--dur-3) var(--ease-out) both; }
  .cv-md foreignObject { overflow: visible !important; }
  .cv-md foreignObject div, .cv-md foreignObject span, .cv-md foreignObject p, .cv-md .nodeLabel, .cv-md .nodeLabel * {
    white-space: normal !important; overflow: visible !important; text-overflow: clip !important;
    word-break: break-word !important; line-height: 1.4 !important; max-width: none !important;
    font-family: var(--font-body) !important;
  }
  .cv-md text { font-family: var(--font-body) !important; }
  .cv-md .node, .cv-md .actor, .cv-md .flowchart-link, .cv-md .messageLine0, .cv-md .messageLine1, .cv-md .messageText {
    transition: opacity var(--dur-2) var(--ease-out), stroke var(--dur-2) var(--ease-out);
  }
  .cv-md .cv-md-node-dim { opacity: .3; }
  .cv-md .cv-md-edge-dim { opacity: .18; }
  .cv-md .cv-md-msg-dim { opacity: .2; }
  .cv-md .cv-md-msg-on { opacity: 1; }
  .cv-md .cv-md-edge-cur { stroke: var(--accent) !important; stroke-width: 2.2px !important; }
  .cv-md .cv-md-edge-flow {
    stroke-dasharray: 1 !important;
    stroke-dashoffset: 1;
    animation: cv-edge-draw var(--motion-draw) var(--ease-in-out) forwards;
  }
  .cv-md .cv-md-pop { animation: cv-fade var(--dur-3) var(--ease-out) both; }
  .cv-md .edgePath:hover path, .cv-md .flowchart-link:hover { stroke-width: 2.2px !important; }
  body[data-theme="product"] .cv-md { padding: 1rem .75rem; background: #fafafa; border: 1px solid var(--line); border-radius: var(--radius); }

  /* =====================================================================
     display = lesson : one board at a time on a single stage
     ===================================================================== */
  body[data-display="lesson"] .frame { width: min(58rem, 94vw); }
  body[data-display="lesson"] #stage.sections { width: 100%; margin-top: 2rem; }
  .cv-lesson { width: 100%; }
  .cv-lesson-stage {
    position: relative;
    display: flex;
    flex-direction: column;
    padding: 1.6rem 2rem 1.5rem;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-lg);
  }
  .cv-lesson-tokens { display: flex; flex-wrap: wrap; justify-content: center; gap: .65rem; margin: 0 0 1.5rem; }
  .cv-lesson-tokens:empty { display: none; }
  .cv-lesson-tokens:has(.on) .cv-tok:not(.on) { opacity: .4; }
  .cv-lesson-tokens .cv-tok.on { border-color: var(--accent); color: var(--accent); transform: translateY(-3px); }
  body[data-theme="product"] .cv-lesson-tokens .cv-tok.on { background: var(--accent-soft); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 12%, transparent); }
  body[data-theme="book"] .cv-lesson-tokens .cv-tok.on { box-shadow: inset 0 -2px 0 var(--accent); }
  .cv-lesson-boards { display: block; min-height: 18rem; }
  .cv-lesson-panel { display: none; border: none; background: transparent; box-shadow: none; border-radius: 0; }
  .cv-lesson-panel.cv-board-on { border: none; box-shadow: none; }
  body[data-theme] .cv-lesson-panel, body[data-theme] .cv-lesson-panel.cv-board-on { border: none; box-shadow: none; background: transparent; }
  .cv-lesson-panel.cv-lesson-active { display: block; animation: cv-fade var(--dur-2) var(--ease-out) both; }
  .cv-lesson-panel .cv-board-head { padding: 0 0 .6rem; cursor: default; }
  .cv-lesson-panel .cv-board-head[hidden] { display: none; }
  .cv-lesson-panel .cv-board-badge { display: none !important; }
  .cv-lesson-panel .cv-board-title { font-size: .85rem; font-weight: 600; color: var(--muted); font-family: var(--font-body); letter-spacing: .02em; }
  .cv-lesson-panel .cv-board-body { padding: 0; gap: 1.35rem; font-size: 1rem; }
  .cv-lesson-panel.cv-lesson-active .cv-block {
    animation: cv-enter var(--dur-3) var(--ease-out) both;
    animation-delay: calc(var(--i, 0) * var(--stagger) + 40ms);
  }
  body[data-dir="back"] .cv-lesson-panel.cv-lesson-active .cv-block { animation-name: cv-enter-back; }
  .cv-lesson-panel .cv-md { min-height: 8rem; }
  .cv-lesson-panel .cv-bar-fill { transition-delay: calc(var(--i, 0) * var(--stagger) + 120ms + var(--j, 0) * 70ms); }
  .cv-lesson-panel .cv-table-wrap { display: flex; justify-content: center; }
  .cv-lesson-panel .cv-table { width: auto; min-width: min(100%, 30rem); }
  .cv-lesson-panel .cv-table td, .cv-lesson-panel .cv-table th { padding: .6rem 1.1rem; }
  .cv-lesson-panel .cv-table td { font-size: .98rem; }

  /* lesson cover (step 0): what this lesson walks through */
  .cv-lesson-toc { display: none; margin: 0; padding: 0; list-style: none; counter-reset: toc; }
  body[data-display="lesson"][data-step="0"] .cv-lesson-toc { display: grid; gap: .15rem; animation: cv-fade var(--dur-3) var(--ease-out) both; }
  body[data-display="lesson"][data-step="0"] .cv-lesson-panel { display: none !important; }
  .cv-lesson-toc li {
    counter-increment: toc;
    display: flex; align-items: baseline; gap: 1rem;
    padding: .7rem .75rem; margin: 0 -.75rem;
    border-radius: var(--radius);
    cursor: pointer;
    transition: background var(--dur-1) var(--ease-out);
    animation: cv-enter var(--dur-3) var(--ease-out) both;
    animation-delay: calc(var(--i, 0) * var(--stagger));
  }
  .cv-lesson-toc li::before {
    content: counter(toc, decimal-leading-zero);
    font-family: var(--font-display); font-variant-numeric: tabular-nums lining-nums;
    font-size: .9rem; color: var(--muted); min-width: 1.6rem;
  }
  .cv-lesson-toc li:hover { background: var(--surface-2); }
  .cv-lesson-toc li:hover::before { color: var(--accent); }
  .cv-lesson-toc .toc-title { font-family: var(--font-display); font-size: 1.12rem; font-weight: 500; color: var(--ink); }
  body[data-theme="book"] .cv-lesson-toc li { border-bottom: 1px solid var(--line); border-radius: 0; }
  body[data-theme="book"] .cv-lesson-toc li:last-child { border-bottom: none; }


  /* ---- sketch: hand drawing on parchment ---- */
  body[data-theme="sketch"] .prose-before > h1 { font-size: clamp(2rem, 3.6vw, 2.75rem); letter-spacing: 0; }
  body[data-theme="sketch"] .cv-subtitle { color: var(--ink-2); }
  body[data-theme="sketch"] button {
    border: 2px solid var(--ink); border-radius: var(--wobble); background-color: var(--surface); font-weight: 400; font-size: .95rem;
  }
  body[data-theme="sketch"] button:hover:not(:disabled) { background-color: var(--surface-2); border-color: var(--ink); }
  body[data-theme="sketch"] .cv-pills { border: 2px solid var(--ink); border-radius: var(--wobble); background: var(--surface); }
  body[data-theme="sketch"] .cv-pills > button, body[data-theme="sketch"] .cv-pills > a { border: none; border-radius: 6px; font-size: .9rem; }
  body[data-theme="sketch"] .cv-pills > [aria-pressed="true"], body[data-theme="sketch"] .cv-pills > [aria-current="page"] {
    background: var(--accent-soft); color: var(--accent); box-shadow: none;
  }
  body[data-theme="sketch"] button#next { background: var(--accent-soft); color: var(--accent); border-color: var(--accent); }
  body[data-theme="sketch"] button#next:hover:not(:disabled) { background: var(--accent); color: #fff; border-color: var(--accent); }
  /* lane / section diagrams: dashed colour frames, coloured numbers, outlined nodes */
  body[data-theme="sketch"] .cv-group-box { stroke: var(--gc); stroke-opacity: .55; stroke-width: 1.6px; stroke-dasharray: 7 6; }
  body[data-theme="sketch"] .cv-group.cv-group-on .cv-group-box { stroke-opacity: 1; stroke-width: 2px; }
  body[data-theme="sketch"] rect.cv-group-badge { fill: none; }
  body[data-theme="sketch"] .cv-group-letter { fill: var(--gc); font-family: var(--font-display); font-size: 22px; }
  body[data-theme="sketch"] .cv-group-title { fill: var(--gc); font-size: 18px; font-weight: 400; }
  body[data-theme="sketch"] .cv-group-sub { font-size: 13px; }
  body[data-theme="sketch"] .cv-group.grole-plain, body[data-theme="sketch"] .cv-group:not([class*="grole-"]) { --gc: var(--hand-1); }
  body[data-theme="sketch"] #diagram g.node rect, body[data-theme="sketch"] #diagram g.node polygon { filter: url(#cv-rough); }
  body[data-theme="sketch"] .cv-node-title { font-size: 16px; font-weight: 400; }
  body[data-theme="sketch"] .cv-node-sub { font-size: 13px; }
  body[data-theme="sketch"] .cv-link, body[data-theme="sketch"] #diagram .edgePath path { stroke: var(--ink); stroke-width: 1.8px; stroke-linecap: round; }
  body[data-theme="sketch"] .cv-label-bg { stroke: none; }
  body[data-theme="sketch"] .cv-edge-text { font-size: 14px; }
  body[data-theme="sketch"] .cv-step-badge { fill: var(--ink); }
  body[data-theme="sketch"] .cv-region { background: transparent; border: 2px solid var(--gc); border-radius: var(--wobble-2); }
  body[data-theme="sketch"] .cv-region-title { color: var(--gc); font-weight: 400; font-size: 1.15rem; }
  body[data-theme="sketch"] span.cv-group-badge { background: transparent; color: var(--gc); font-family: var(--font-display); font-size: 1.15rem; min-width: 0; padding: 0; }
  body[data-theme="sketch"] .cv-bar { stroke: var(--ink); stroke-width: 1.8px; stroke-linejoin: round; }
  /* boards: each board takes the next marker colour, like sections on a whiteboard */
  body[data-theme="sketch"] .cv-board { --hc: var(--hand-1); background: transparent; border: 2px solid var(--hc); border-radius: var(--wobble-2); }
  body[data-theme="sketch"] .cv-board:nth-child(4n+2 of .cv-board) { --hc: var(--hand-2); border-radius: var(--wobble); }
  body[data-theme="sketch"] .cv-board:nth-child(4n+3 of .cv-board) { --hc: var(--hand-3); }
  body[data-theme="sketch"] .cv-board:nth-child(4n+4 of .cv-board) { --hc: var(--hand-4); border-radius: var(--wobble); }
  body[data-theme="sketch"] .cv-board-title { color: var(--hc); font-weight: 400; font-size: 1.15rem; }
  body[data-theme="sketch"] .cv-board-badge { background: transparent; color: var(--hc); font-family: var(--font-display); font-size: 1.2rem; min-width: 0; padding: 0; }
  body[data-theme="sketch"] .cv-board-badge::after { content: "."; }
  body[data-theme="sketch"] .cv-board.cv-board-on { border-width: 3px; box-shadow: none; }
  body[data-theme="sketch"] .cv-board.cv-board-on .cv-board-badge { background: transparent; color: var(--hc); }
  body[data-theme="sketch"] .cv-tok { border: 2px solid var(--ink); border-radius: var(--wobble); background: var(--surface); font-weight: 400; }
  body[data-theme="sketch"] .cv-tok-idx { font-family: var(--font-body); font-size: .8rem; }
  body[data-theme="sketch"] .cv-tok.cv-tok-focus { border-color: var(--accent); color: var(--accent); }
  body[data-theme="sketch"] .cv-callout { padding: .15rem 0 .15rem 1rem; border-left: 3px solid var(--accent); border-radius: 0; }
  body[data-theme="sketch"] .cv-callout p { font-size: 1.08rem; }
  body[data-theme="sketch"] .cv-callout.tone-warn { border-left-color: var(--warn); }
  body[data-theme="sketch"] .cv-callout.tone-ok { border-left-color: var(--ok); }
  body[data-theme="sketch"] .cv-sym.ok { background: var(--ok); border-radius: 3px; }
  body[data-theme="sketch"] .cv-sym.bad { background: transparent; color: var(--bad); font-size: 1.15em; font-weight: 900; }
  body[data-theme="sketch"] .cv-sym.warn { background: transparent; color: var(--warn); font-size: 1.15em; font-weight: 900; }
  body[data-theme="sketch"] .cv-table { font-size: .98rem; }
  body[data-theme="sketch"] .cv-table thead th { border-bottom: 2px solid var(--ink); color: var(--ink-2); font-size: .95rem; font-weight: 400; }
  body[data-theme="sketch"] .cv-table td { border-top: 1.5px dashed var(--line); }
  body[data-theme="sketch"] .cv-tr.cv-tr-focus td { background: var(--accent-soft); color: var(--accent); }
  body[data-theme="sketch"] .cv-bar-label { font-weight: 400; font-size: 1rem; }
  body[data-theme="sketch"] .cv-bar-track { height: 14px; border: 2px solid var(--ink); border-radius: var(--wobble); background: transparent; }
  body[data-theme="sketch"] .cv-bar-fill { border-radius: 0; }
  body[data-theme="sketch"] .cv-bar-row.cv-bar-focus { background: transparent; }
  body[data-theme="sketch"] .cv-bar-row.cv-bar-focus .cv-bar-label { color: var(--accent); }
  body[data-theme="sketch"] .cv-bar-row.cv-bar-focus .cv-bar-fill { background: repeating-linear-gradient(-45deg, var(--bad) 0 2px, transparent 2px 6px); }
  body[data-theme="sketch"] .cv-bar-val { color: var(--ink-2); font-size: 1rem; }
  body[data-theme="sketch"] .cv-code { background: transparent; border: 2px dashed var(--line); }
  /* lesson: the whole stage is the whiteboard; no card around it */
  body[data-theme="sketch"] .cv-lesson-stage { background: transparent; border: none; box-shadow: none; padding: .5rem 0 0; }
  body[data-theme="sketch"] .cv-lesson-tokens .cv-tok.on { color: var(--accent); background: var(--accent-soft); border-color: var(--accent); transform: translateY(-3px) rotate(-1deg); }
  body[data-theme="sketch"] .cv-lesson-toc li { border-radius: 0; }
  body[data-theme="sketch"] .cv-lesson-toc li::before { content: counter(toc) "."; font-size: 1.3rem; color: var(--hand-1); }
  body[data-theme="sketch"] .cv-lesson-toc li:nth-child(4n+2)::before { color: var(--hand-2); }
  body[data-theme="sketch"] .cv-lesson-toc li:nth-child(4n+3)::before { color: var(--hand-3); }
  body[data-theme="sketch"] .cv-lesson-toc li:nth-child(4n+4)::before { color: var(--hand-4); }
  body[data-theme="sketch"] .cv-lesson-toc .toc-title { font-weight: 400; font-size: 1.2rem; }
  body[data-theme="sketch"] .cv-lesson-toc li:hover { background: transparent; }
  body[data-theme="sketch"] .cv-lesson-toc li:hover .toc-title { color: var(--accent); }

  /* =====================================================================
     Motion. One-shot only: nothing loops while the reader is reading.
     ===================================================================== */
  @keyframes cv-fade { from { opacity: 0; } to { opacity: 1; } }
  @keyframes cv-rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
  @keyframes cv-enter {
    from { opacity: 0; transform: translate(var(--enter-x), var(--enter-y)) scale(var(--enter-scale)) rotate(var(--enter-rot)); }
    to { opacity: 1; transform: none; }
  }
  @keyframes cv-enter-back {
    from { opacity: 0; transform: translate(calc(var(--enter-x) * -1), var(--enter-y)) scale(var(--enter-scale)) rotate(calc(var(--enter-rot) * -1)); }
    to { opacity: 1; transform: none; }
  }
  @keyframes cv-edge-draw { from { stroke-dashoffset: 1; } to { stroke-dashoffset: 0; } }
  @keyframes cv-stroke-in { from { stroke-opacity: .15; } to { stroke-opacity: 1; } }
  @keyframes cv-badge-pop { 0% { transform: scale(.6); } 60% { transform: scale(1.12); } 100% { transform: scale(1); } }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: .01ms !important;
      animation-delay: 0s !important;
      animation-iteration-count: 1 !important;
      transition-duration: .01ms !important;
    }
  }
</style>
</head>
<body data-step="0" data-theme="/*THEME*/" data-mode="/*MODE*/" data-orientation="/*ORIENTATION*/" data-display="/*DISPLAY*/">
  <svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false"><defs>
    <filter id="cv-rough" x="-4%" y="-4%" width="108%" height="108%">
      <feTurbulence type="fractalNoise" baseFrequency="0.03" numOctaves="2" seed="5" result="noise"/>
      <feDisplacementMap in="SourceGraphic" in2="noise" scale="2.6" xChannelSelector="R" yChannelSelector="G"/>
    </filter>
  </defs></svg>
  <header class="cv-toolbar" id="toolbar">
    <div class="cv-tb-nav" role="group" aria-label="播放控制">
      <button type="button" id="prev" title="上一步（←）" aria-label="上一步">‹</button>
      <button type="button" id="play" aria-pressed="false" title="自动播放（空格）">▶ 播放</button>
      <button type="button" id="next" title="下一步（→）">下一步 ›</button>
      <button type="button" id="reset" title="回到开头（R）" aria-label="重来">↺</button>
    </div>
    <div class="caption-box" id="caption">
      <span class="counter" id="counter">0 / 0</span>
      <div class="cap-wrap"><span class="cap" id="cap"></span><span class="cap-sub" id="capsub"></span></div>
      <pre class="detail" id="detail" hidden></pre>
    </div>
    <div class="cv-tb-right">
      <nav class="cv-pills" id="views" aria-label="视图" hidden>
        <a data-view="sheet" title="所有板平铺在一张图上">平铺</a>
        <a data-view="deck" title="平铺，只亮当前这块">聚焦</a>
        <a data-view="lesson" title="一次看一块">逐页</a>
      </nav>
      <div class="cv-pills" id="theme" role="group" aria-label="主题">
        <button type="button" data-theme-pick="book">书页</button>
        <button type="button" data-theme-pick="product">产品</button>
        <button type="button" data-theme-pick="sketch">手绘</button>
      </div>
    </div>
    <div class="cv-progress" aria-hidden="true"><i id="progress"></i></div>
  </header>
  <main class="frame">
    <div class="prose-before"><!--BEFORE--></div>
    <div id="stage"><div id="diagram"></div></div>
    <!--CHART-->
    <!--EXPLAIN_OPEN-->
    <!--EXPLAIN_NOTES-->
    <!--EXPLAIN_CLOSE-->
    <div class="prose-after"><!--AFTER--></div>
  </main>
  <script>
    const DATA = /*DATA*/;
    window.DATA = DATA;
    const host = document.getElementById("diagram");
    const STEPS = DATA.steps || [];
    let step = 0;          // number of steps played, 0..STEPS.length
    let upgraded = false;
    let packetAnim = 0;
    const VIDEO = DATA.mode === "video";
    if (!VIDEO) {
      const urlTheme = new URLSearchParams(location.search).get("theme");
      if (urlTheme && document.querySelector('[data-theme-pick="' + CSS.escape(urlTheme) + '"]')) document.body.dataset.theme = urlTheme;
    }

    function nodeIdFromGroup(group) {
      const ready = group.getAttribute("data-node-id");
      if (ready) return ready;
      const raw = group.id || "";
      for (const node of DATA.nodes) {
        const head = "flowchart-" + node.id + "-";
        const at = raw.indexOf(head);
        if (at === 0 || (at > 0 && raw.charAt(at - 1) === "-")) {
          if (/^[0-9]+$/.test(raw.slice(at + head.length))) return node.id;
        }
      }
      return "";
    }

    function edgeKeyFromId(raw) {
      if (!raw) return "";
      for (const edge of DATA.edges) {
        if (raw === edge.key || raw.endsWith("-" + edge.key) || raw.endsWith("_" + edge.key)) return edge.key;
      }
      return "";
    }

    function tagMermaid() {
      host.querySelectorAll("g.node").forEach((g) => {
        const id = nodeIdFromGroup(g);
        if (id) g.setAttribute("data-node-id", id);
      });
      const paths = host.querySelectorAll("path.flowchart-link, .edgePaths path, .edgePath path");
      paths.forEach((p) => {
        const key = edgeKeyFromId(p.id) || edgeKeyFromId(p.getAttribute("data-id"));
        if (key) p.setAttribute("data-edge-key", key);
      });
      host.querySelectorAll(".edgeLabel").forEach((lab) => {
        const inner = lab.querySelector("[data-id]");
        const key = edgeKeyFromId(inner && inner.getAttribute("data-id")) || edgeKeyFromId(lab.id);
        if (key) lab.setAttribute("data-edge-key", key);
      });
    }

    function addHitPaths() {
      const svg = host.querySelector("svg");
      host.querySelectorAll("path[data-edge-key]:not(.cv-hit)").forEach((p) => {
        p.classList.add("cv-edge");
        const hit = document.createElementNS("http://www.w3.org/2000/svg", "path");
        hit.setAttribute("d", p.getAttribute("d"));
        hit.setAttribute("class", "cv-hit");
        hit.setAttribute("data-edge-key", p.getAttribute("data-edge-key"));
        const t = p.getAttribute("transform");
        if (t) hit.setAttribute("transform", t);
        p.parentNode.insertBefore(hit, p.nextSibling);
      });
      if (svg && !svg.querySelector("#cv-packet")) {
        const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.setAttribute("id", "cv-packet");
        dot.setAttribute("r", "0");
        svg.appendChild(dot);
      }
    }

    const INVOLVED = new Set(DATA.start || []);
    STEPS.forEach((it) => {
      (it.nodes || []).forEach((n) => INVOLVED.add(n));
      (it.focus || []).forEach((n) => INVOLVED.add(n));
      (it.reveal_groups || []).forEach((n) => INVOLVED.add(n));
    });

    function stateAt(k) {
      const lit = new Set(DATA.start || []);
      const done = new Set();
      let current = new Set(DATA.start || []);
      let flow = new Set();
      for (let i = 0; i < k; i++) {
        (STEPS[i].edges || []).forEach((e) => done.add(e));
        (STEPS[i].nodes || []).forEach((n) => lit.add(n));
        (STEPS[i].focus || []).forEach((n) => lit.add(n));
      }
      if (k > 0) {
        current = new Set(STEPS[k - 1].focus || []);
        flow = new Set(STEPS[k - 1].edges || []);
      }
      const next = new Set(k < STEPS.length ? STEPS[k].edges : []);
      return { lit, done, current, flow, next };
    }

    let prevLit = new Set();
    let smoothCam = false;
    let lastCap = null;
    let paintedStep = -1;  // boards: the step the bars were last filled for

    function setCaption(text, sub) {
      const n = STEPS.length;
      document.getElementById("counter").innerHTML = "<b>" + step + "</b> / " + n;
      const bar = document.getElementById("progress");
      if (bar) bar.style.width = (n ? (100 * step / n) : 0) + "%";
      const cap = document.getElementById("cap");
      const capSub = document.getElementById("capsub");
      if (capSub) capSub.textContent = sub || "";
      if (text === lastCap) return;
      lastCap = text;
      cap.textContent = text;
      cap.classList.remove("cv-in");
      void cap.offsetWidth;
      cap.classList.add("cv-in");
    }

    function introCaption(kind) {
      const n = STEPS.length;
      if (!n) return "这一页没有步骤。";
      if (kind === "sheet") return "所有板子都摊开了。一共 " + n + " 步，按 → 一行一行往下看。";
      if (kind === "lesson") return "一共 " + n + " 步。按 →，或者点下面任意一步开始。";
      return "一共 " + n + " 步，按 → 开始。";
    }

    function paintBoards(animate) {
      const boards = [...host.querySelectorAll(".cv-board")];
      let revealed = null;
      let current = null;
      let intra = null;
      let prevGroup = null;
      if (step === 0) {
        revealed = null;
      } else {
        const it = STEPS[step - 1] || {};
        revealed = new Set(it.reveal_groups || []);
        current = it.group || null;
        intra = it.intra || null;
        prevGroup = step > 1 ? (STEPS[step - 2] || {}).group : null;
      }
      const boardJustEntered = !!current && current !== prevGroup;
      const freshStep = step !== paintedStep;
      paintedStep = step;

      const disp = DATA.display || "sheet";
      const sheet = disp === "sheet";
      const lesson = disp === "lesson";
      boards.forEach((board) => {
        const id = board.getAttribute("data-group-id");
        const isCur = !!current && id === current;
        let on, doneBoard;
        if (sheet) {
          on = true;
          doneBoard = !isCur && step > 0;
        } else if (lesson) {
          on = isCur;
          doneBoard = false;
        } else {
          on = (revealed === null || revealed.has(id));
          doneBoard = on && !isCur && revealed !== null;
        }
        board.classList.toggle("cv-lit", on);
        board.classList.toggle("cv-dim", !sheet && !lesson && step > 0 && !on);
        board.classList.toggle("cv-board-on", isCur);
        board.classList.toggle("cv-lesson-active", lesson && isCur);
        board.hidden = !!(lesson && !on);
        if (isCur && boardJustEntered && animate) {
          board.classList.remove("cv-board-enter");
          void board.offsetWidth;
          board.classList.add("cv-board-enter");
          if (!sheet && !lesson) {
            try { board.scrollIntoView({ behavior: "smooth", block: "nearest" }); } catch (e) {}
          }
        }
        paintBoardIntra(board, {
          overview: sheet ? (!isCur || step === 0) : (lesson ? true : (revealed === null)),
          on: on,
          isCur: isCur,
          doneBoard: lesson ? true : doneBoard,
          intra: lesson ? { kind: "done", i: 999, n: 1, block: 0 } : (isCur ? intra : ((doneBoard || sheet) ? { kind: "done", i: 999, n: 1, block: 0 } : null)),
          animate: animate && isCur,
          enter: isCur && lesson && freshStep
        });
      });

      // lesson: re-render mermaid when a panel becomes visible (hidden→shown breaks layout)
      if (lesson && window.mermaid) {
        const active = boards.find((b) => b.classList.contains("cv-lesson-active") && !b.hidden);
        if (active) {
          const needs = [...active.querySelectorAll(".cv-md")].some((el) => {
            return el.dataset.mdReady !== "1" || mdSvgBad(el.querySelector("svg"));
          });
          if (needs) renderBoardMermaids(active, true);
        }
      }
      // lesson persistent token highlight
      if (lesson) {
        let focus = null;
        if (step > 0 && current) {
          const curBoard = boards.find(b => b.getAttribute("data-group-id") === current);
          const raw = curBoard && curBoard.getAttribute("data-focus-tokens");
          if (raw) focus = raw.split(",").map(Number);
        }
        document.querySelectorAll(".cv-lesson-tokens .cv-tok").forEach((el) => {
          const i = Number(el.getAttribute("data-i"));
          el.classList.toggle("on", focus != null && focus.includes(i));
        });
      }

      if (step === 0) {
        setCaption(introCaption(disp), "");
      } else {
        const it = STEPS[step - 1];
        setCaption((it.optional ? "（可选）" : "") + (it.caption || ""), it.sub || "");
      }
      document.getElementById("prev").disabled = step <= 0;
      document.getElementById("next").disabled = step >= STEPS.length;
      document.getElementById("reset").disabled = step <= 0;
      document.body.dataset.step = String(step);
      if (intra) document.body.dataset.intra = intra.kind + ":" + intra.i;
      else document.body.dataset.intra = step === 0 ? "overview" : "";
    }

    function paintBoardIntra(board, st) {
      const intra = st.intra;
      const kind = intra && intra.kind;
      const ii = intra ? intra.i : -1;
      const block = intra && intra.block != null ? intra.block : -1;

      // callout soft enter
      board.querySelectorAll(".cv-callout").forEach((call) => {
        const blk = call.closest(".cv-block");
        const bi = blk ? Number(blk.getAttribute("data-block")) : 0;
        const active = st.overview || st.doneBoard || (st.isCur && kind === "callout" && bi === block);
        const pending = st.isCur && kind === "callout" && bi === block;
        call.classList.toggle("cv-callout-on", !!pending);
        call.classList.toggle("cv-callout-dim", st.isCur && kind === "callout" && bi !== block);
        if (pending && st.animate) {
          call.classList.remove("cv-callout-enter");
          void call.offsetWidth;
          call.classList.add("cv-callout-enter");
        }
      });


      // token chips
      board.querySelectorAll(".cv-tok").forEach((tok) => {
        const blk = tok.closest(".cv-block");
        if (!blk || blk.getAttribute("data-kind") !== "chips") return;
        const bi = Number(blk.getAttribute("data-block"));
        const ti = Number(tok.getAttribute("data-i"));
        let show = st.overview || st.doneBoard;
        let focus = false;
        if (st.isCur && kind === "chips" && bi === block) {
          show = ti <= ii;
          focus = ti === ii;
        } else if (st.isCur && !(st.overview || st.doneBoard) && bi < block) {
          show = true;
        } else if (st.isCur && !(st.overview || st.doneBoard) && bi > block) {
          show = false;
        }
        tok.classList.toggle("on", show);
        tok.classList.toggle("cv-tok-focus", focus);
      });

      // list items
      board.querySelectorAll(".cv-li").forEach((li) => {
        const blk = li.closest(".cv-block");
        const bi = blk ? Number(blk.getAttribute("data-block")) : 0;
        const liI = Number(li.getAttribute("data-li"));
        let show = st.overview || st.doneBoard;
        let focus = false;
        if (st.isCur && kind === "list" && bi === block) {
          show = liI <= ii;
          focus = liI === ii;
        } else if (st.isCur && !(st.overview || st.doneBoard) && (kind !== "list" || bi !== block)) {
          // other block on same board: show if earlier block fully done
          show = bi < block || (bi === block && false);
          if (bi < block) show = true;
        }
        li.classList.toggle("cv-li-on", show);
        li.classList.toggle("cv-li-focus", focus);
        li.classList.toggle("cv-li-pending", !show);
        if (focus && st.animate) {
          li.classList.remove("cv-li-enter");
          void li.offsetWidth;
          li.classList.add("cv-li-enter");
        }
      });

      // table row-by-row
      board.querySelectorAll(".cv-tr").forEach((tr) => {
        const blk = tr.closest(".cv-block");
        const bi = blk ? Number(blk.getAttribute("data-block")) : 0;
        const ri = Number(tr.getAttribute("data-row"));
        let show = st.overview || st.doneBoard;
        let focus = false;
        if (st.isCur && kind === "table" && bi === block) {
          show = ri <= ii;
          focus = ri === ii;
        } else if (st.isCur && !(st.overview || st.doneBoard)) {
          show = bi < block;
        }
        tr.classList.toggle("cv-tr-pending", !show);
        tr.classList.toggle("cv-tr-shown", show);
        tr.classList.toggle("cv-tr-focus", focus);
        tr.classList.toggle("cv-tr-on", focus);
        if (focus && st.animate) {
          tr.classList.remove("cv-tr-enter");
          void tr.offsetWidth;
          tr.classList.add("cv-tr-enter");
        }
      });

      // bars needle + fill
      board.querySelectorAll(".cv-bar-row").forEach((row) => {
        const blk = row.closest(".cv-block");
        const bi = blk ? Number(blk.getAttribute("data-block")) : 0;
        const barI = Number(row.getAttribute("data-bar"));
        const pct = row.getAttribute("data-pct") || "0";
        const needle = row.querySelector(".cv-bar-needle");
        const fill = row.querySelector(".cv-bar-fill");
        let live = st.overview || st.doneBoard;
        let focus = false;
        if (st.isCur && kind === "bars" && bi === block) {
          live = barI <= ii;
          focus = barI === ii;
        } else if (st.isCur && !(st.overview || st.doneBoard)) {
          live = bi < block;
        }
        row.classList.toggle("cv-bar-live", live);
        row.classList.toggle("cv-bar-focus", focus);
        row.classList.toggle("cv-bar-pending", !live);
        const target = live ? pct : "0";
        if (needle) needle.style.left = target + "%";
        if (fill) {
          if (st.enter) {
            fill.style.transition = "none";
            fill.style.width = "0%";
            void fill.offsetWidth;
            fill.style.transition = "";
            requestAnimationFrame(() => { fill.style.width = target + "%"; });
          } else {
            fill.style.transition = st.animate ? "" : "none";
            requestAnimationFrame(() => { fill.style.width = target + "%"; });
          }
        }
      });

      // code line walk
      board.querySelectorAll(".cv-code-line").forEach((ln) => {
        const blk = ln.closest(".cv-block");
        const bi = blk ? Number(blk.getAttribute("data-block")) : 0;
        const li = Number(ln.getAttribute("data-line"));
        let show = st.overview || st.doneBoard;
        let focus = false;
        if (st.isCur && kind === "code" && bi === block) {
          show = li <= ii;
          focus = li === ii;
        } else if (st.isCur && !(st.overview || st.doneBoard)) {
          show = bi < block;
        }
        ln.classList.toggle("cv-code-pending", !show);
        ln.classList.toggle("cv-code-shown", show);
        ln.classList.toggle("cv-code-focus", focus);
        if (focus && st.animate) {
          ln.classList.remove("cv-code-enter");
          void ln.offsetWidth;
          ln.classList.add("cv-code-enter");
        }
      });

      // flowchart / sequence path light-up
      board.querySelectorAll(".cv-md").forEach((md) => {
        const bi = Number(md.getAttribute("data-md-block") || 0);
        let upto = -1;
        if (st.overview || st.doneBoard) upto = 999;
        else if (st.isCur && (kind === "flowchart" || kind === "sequence" || kind === "mermaid") && bi === block) upto = ii;
        else if (st.isCur && bi < block) upto = 999;
        md.classList.toggle("cv-md-live", upto >= 0);
        stepMermaid(md, upto, st.animate && st.isCur && bi === block && upto === ii);
      });
    }

    function stepMermaid(md, upto, animateCurrent) {
      if (!md) return;
      const svg = md.querySelector("svg");
      if (!svg) return;
      const kind = md.getAttribute("data-md-kind") || "flowchart";
      let edges = [...svg.querySelectorAll(".edgePaths path.flowchart-link, .edgePath path, path.flowchart-link")];
      if (!edges.length) {
        edges = [...svg.querySelectorAll("path.messageLine0, path.messageLine1, line.messageLine0, line.messageLine1, .messageLine0, .messageLine1")];
      }
      if (!edges.length) {
        edges = [...svg.querySelectorAll(".flowchart-link, path[class*='message']")];
      }
      const nodes = [...svg.querySelectorAll("g.node, g.actor")];
      const msgs = [...svg.querySelectorAll("g.messageText, .messageText, text.messageText")];

      edges.forEach((e, i) => {
        const on = i <= upto;
        const cur = i === upto && upto < 999;
        e.classList.toggle("cv-md-edge-dim", !on);
        e.classList.toggle("cv-md-edge-on", on);
        e.classList.toggle("cv-md-edge-cur", cur);
        if (cur && animateCurrent) {
          try { e.setAttribute("pathLength", "1"); } catch (err) {}
          e.classList.remove("cv-md-edge-flow");
          void e.getBoundingClientRect();
          e.classList.add("cv-md-edge-flow");
        }
      });
      // nodes: light those touched by edges 0..upto (approx: first upto+1 nodes + decision style)
      nodes.forEach((n, i) => {
        const on = upto >= 999 || i <= Math.min(nodes.length - 1, upto + 1);
        n.classList.toggle("cv-md-node-dim", !on);
        n.classList.toggle("cv-md-node-on", on);
        if (on && i === Math.min(nodes.length - 1, upto + 1) && animateCurrent) {
          n.classList.remove("cv-md-pop");
          void n.offsetWidth;
          n.classList.add("cv-md-pop");
        }
      });
      msgs.forEach((m, i) => {
        const on = i <= upto;
        m.classList.toggle("cv-md-msg-dim", !on);
        m.classList.toggle("cv-md-msg-on", on);
      });
    }


    function paint(animate) {
      if (DATA.boards) {
        paintBoards(animate);
        return;
      }
      const s = stateAt(step);
      const is3b = VIDEO;
      host.querySelectorAll("g.node[data-node-id]").forEach((g) => {
        const id = g.getAttribute("data-node-id");
        const on = !INVOLVED.has(id) || s.lit.has(id) || s.current.has(id);
        g.classList.toggle("cv-lit", on);
        g.classList.toggle("cv-dim", !on);
        g.classList.remove("cv-current");
        if (is3b && animate && on && INVOLVED.has(id) && !prevLit.has(id)) {
          g.classList.remove("cv-draw");
          g.querySelectorAll("rect, polygon, path, circle, ellipse").forEach((sh) => sh.setAttribute("pathLength", "1"));
          void g.getBoundingClientRect();
          g.classList.add("cv-draw");
          setTimeout(() => g.classList.remove("cv-draw"), 1300);
        }
        if (s.current.has(id)) {
          void g.getBoundingClientRect();
          g.classList.add("cv-current");
        }
      });
      host.querySelectorAll("[data-edge-key]:not(g.node)").forEach((el) => {
        const key = el.getAttribute("data-edge-key");
        el.classList.remove("cv-flow", "cv-done", "cv-next", "cv-future", "cv-cur");
        const isPath = el.tagName.toLowerCase() === "path";
        if (isPath && !VIDEO) el.removeAttribute("pathLength");
        if (s.flow.has(key) && animate) {
          if (isPath) el.setAttribute("pathLength", "1");
          void el.getBoundingClientRect();
          el.classList.add("cv-flow");
          if (isPath) {
            el.addEventListener("animationend", () => {
              if (el.classList.contains("cv-flow")) {
                el.classList.remove("cv-flow"); el.classList.add("cv-done"); el.removeAttribute("pathLength");
                if (!VIDEO) el.classList.add("cv-cur");
              }
            }, { once: true });
          }
        } else if (s.done.has(key)) el.classList.add("cv-done");
        else if (s.next.has(key)) el.classList.add("cv-next");
        else el.classList.add("cv-future");
        if (s.flow.has(key) && !animate) { el.classList.add("cv-done"); if (!VIDEO) el.classList.add("cv-cur"); }
        if (s.flow.has(key) && animate && !isPath && !VIDEO) el.classList.add("cv-cur");
      });
      if (step === 0) {
        setCaption(introCaption("graph"), "");
      } else {
        const it = STEPS[step - 1];
        setCaption((it.optional && !/可选|optional/i.test(it.caption) ? "（可选）" : "") + it.caption, "");
      }
      const detail = document.getElementById("detail");
      const d = step > 0 ? (STEPS[step - 1].detail || "") : "";
      detail.hidden = !d;
      detail.textContent = d;
      detail.classList.remove("cv-in"); void detail.offsetWidth; if (d) detail.classList.add("cv-in");
      paintChart();
      document.getElementById("prev").disabled = step === 0;
      document.getElementById("next").disabled = step >= STEPS.length;
      document.getElementById("reset").disabled = step === 0;
      document.body.setAttribute("data-step", String(step));
      document.body.setAttribute("data-total", String(STEPS.length));
      if (animate && step > 0) runPacket(STEPS[step - 1].edges);
      paintGroups(s);
      prevLit = new Set([...s.lit, ...s.current]);
      camera(animate || smoothCam);
      smoothCam = false;
    }

    /* ---- camera (video renderer only): animate the SVG viewBox ---- */
    let fullBox = null;
    let camAnim = 0;
    let overviewMode = false;
    function svgEl() { return host.querySelector("svg"); }
    function readBox(svg) {
      const v = (svg.getAttribute("viewBox") || "").trim().split(/[\\s,]+/).map(Number);
      return v.length === 4 && v.every(Number.isFinite) ? v : null;
    }
    function setBox(svg, b) {
      svg.setAttribute("viewBox", b.map((n) => n.toFixed(2)).join(" "));
      host.setAttribute("data-camera", b.map((n) => n.toFixed(1)).join(" "));
    }
    function userRect(svg, el) {
      const r = el.getBoundingClientRect();
      const m = svg.getScreenCTM();
      if (!m || !r.width && !r.height) return null;
      const inv = m.inverse();
      const p = svg.createSVGPoint();
      p.x = r.left; p.y = r.top; const a = p.matrixTransform(inv);
      p.x = r.right; p.y = r.bottom; const b = p.matrixTransform(inv);
      return [Math.min(a.x, b.x), Math.min(a.y, b.y), Math.max(a.x, b.x), Math.max(a.y, b.y)];
    }
    function focusBox(svg) {
      if (step === 0 || !fullBox) return fullBox;
      const it = STEPS[step - 1];
      const els = [];
      new Set([...it.focus, ...it.nodes]).forEach((id) => {
        const g = host.querySelector('g.node[data-node-id="' + CSS.escape(id) + '"]');
        if (g) els.push(g);
      });
      it.edges.forEach((k) => host.querySelectorAll('[data-edge-key="' + CSS.escape(k) + '"]:not(.cv-hit)').forEach((e) => els.push(e)));
      // keep the title of every region that holds a focused node in frame
      if (!VIDEO) host.querySelectorAll(".cv-group[data-members]").forEach((g) => {
        const members = (g.getAttribute("data-members") || "").split(" ");
        if (it.focus.some((f) => members.includes(f))) {
          const head = g.querySelector(".cv-group-head");
          if (head) els.push(head);
        }
      });
      let box = null;
      els.forEach((el) => {
        const r = userRect(svg, el);
        if (!r) return;
        box = box ? [Math.min(box[0], r[0]), Math.min(box[1], r[1]), Math.max(box[2], r[2]), Math.max(box[3], r[3])] : r;
      });
      if (!box) return fullBox;
      const [fx, fy, fw, fh] = fullBox;
      const pad = Math.max(fw, fh) * 0.04;
      let w = Math.max(box[2] - box[0] + 2 * pad, fw * 0.36);
      let h = Math.max(box[3] - box[1] + 2 * pad, fh * 0.36);
      const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
      if (VIDEO) {
        // video: frame the focus at the stage's aspect (16:9-ish), leaving room around it
        const ratio = (svg.clientWidth || 16) / (svg.clientHeight || 9);
        w = Math.max(box[2] - box[0] + 2 * pad, fw * 0.3) * 1.08;
        h = Math.max(box[3] - box[1] + 2 * pad, fh * 0.22) * 1.08;
        if (w / h > ratio) h = w / ratio; else w = h * ratio;
        const fr = fw / fh;
        let fullW = fw, fullH = fh;
        if (fr > ratio) fullH = fw / ratio; else fullW = fh * ratio;
        if (w >= fullW) return [fx + fw / 2 - fullW / 2, fy + fh / 2 - fullH / 2, fullW, fullH];
        const clampC = (c, size, lo, span) => (size >= span ? lo + span / 2 : Math.min(Math.max(c, lo + size / 2), lo + span - size / 2));
        return [clampC(cx, w, fx, fw) - w / 2, clampC(cy, h, fy, fh) - h / 2, w, h];
      }
      const ratio = fw / fh;
      if (w / h > ratio) h = w / ratio; else w = h * ratio;
      if (w > fw) { w = fw; h = fh; }
      let x = Math.min(Math.max(cx - w / 2, fx), fx + fw - w);
      let y = Math.min(Math.max(cy - h / 2, fy), fy + fh - h);
      return [x, y, w, h];
    }
    function camera(animate) {
      const svg = svgEl();
      if (!svg) return;
      if (!fullBox) fullBox = readBox(svg);
      if (!fullBox) return;
      const is3b = VIDEO;
      const wantOverview = !is3b || step === 0 || overviewMode;
      document.body.classList.toggle("cv-overview", is3b && wantOverview);
      host.setAttribute("data-camera-mode", wantOverview ? "overview" : "focus");
      host.setAttribute("data-camera-full", fullBox.map((n) => n.toFixed(1)).join(" "));
      // measure in full-view coordinates so the target does not depend on the current zoom
      const from = readBox(svg) || fullBox;
      let target = fullBox;
      if (!wantOverview) {
        setBox(svg, fullBox);
        target = focusBox(svg) || fullBox;
        setBox(svg, from);
      }
      const id = ++camAnim;
      if (!animate || !is3b) { setBox(svg, target); return; }
      const t0 = performance.now(), dur = 750;
      function frame(now) {
        if (id !== camAnim) return;
        const f = Math.min(1, (now - t0) / dur);
        const e = f < .5 ? 4 * f * f * f : 1 - Math.pow(-2 * f + 2, 3) / 2;
        setBox(svg, from.map((v, i) => v + (target[i] - v) * e));
        if (f < 1) requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    }

    function runPacket(keys) {
      const svg = host.querySelector("svg");
      const dot = svg && svg.querySelector("#cv-packet");
      const path = keys.length && host.querySelector('path.cv-edge[data-edge-key="' + keys[0] + '"]');
      if (!dot || !path || !path.getTotalLength) return;
      const len = path.getTotalLength();
      if (!len) return;
      const id = ++packetAnim;
      const t0 = performance.now();
      const dur = 850;
      dot.setAttribute("r", "5");
      function frame(now) {
        if (id !== packetAnim) return;
        const f = Math.min(1, (now - t0) / dur);
        const ease = f < .5 ? 2 * f * f : 1 - Math.pow(-2 * f + 2, 2) / 2;
        const pt = path.getPointAtLength(ease * len);
        const a = path.getScreenCTM(), b = svg.getScreenCTM();
        let x = pt.x, y = pt.y;
        if (a && b) {
          const p = svg.createSVGPoint(); p.x = pt.x; p.y = pt.y;
          const q = p.matrixTransform(a).matrixTransform(b.inverse());
          x = q.x; y = q.y;
        }
        dot.setAttribute("cx", x); dot.setAttribute("cy", y);
        if (f < 1) requestAnimationFrame(frame);
        else dot.setAttribute("r", "0");
      }
      requestAnimationFrame(frame);
    }

    /* ---- chart block ---- */
    const CH = DATA.chart;
    const chartEl = document.getElementById("chart");
    const ANY_REVEAL = STEPS.some((it) => it.reveal !== undefined);
    let pinned = null;
    let hover = null;
    function chartState() {
      if (!CH) return null;
      let rv = ANY_REVEAL ? 0 : CH.categories.length;
      for (let i = 0; i < step; i++) if (STEPS[i].reveal !== undefined) rv = STEPS[i].reveal;
      const stepHl = step > 0 && STEPS[step - 1].highlight !== undefined ? STEPS[step - 1].highlight : null;
      const hl = hover !== null ? hover : (pinned !== null ? pinned : stepHl);
      return { reveal: rv, hl: hl !== null && hl < rv ? hl : null };
    }
    function paintChart() {
      if (!CH || !chartEl) return;
      const st = chartState();
      chartEl.querySelectorAll("[data-cat]").forEach((el) => {
        if (el.classList.contains("cv-hitcol")) return;
        const c = Number(el.getAttribute("data-cat"));
        el.classList.toggle("cv-hidden", c >= st.reveal);
        el.classList.toggle("cv-hl", st.hl === c);
        el.classList.toggle("cv-faded", st.hl !== null && st.hl !== c && el.classList.contains("cv-mark"));
      });
      chartEl.setAttribute("data-reveal", String(st.reveal));
      chartEl.setAttribute("data-highlight", st.hl === null ? "" : String(st.hl));
      const out = document.getElementById("chart-readout");
      if (st.hl === null) {
        out.textContent = st.reveal < CH.categories.length
          ? "已显示 " + st.reveal + " / " + CH.categories.length + " 组。把鼠标移到柱子上能看到数值。"
          : "把鼠标移到柱子上能看到数值，点一下可以固定。";
      } else {
        const parts = CH.series.map((s) => s.name + " <b>" + (s.values[st.hl] === null ? "—" : s.values[st.hl]) + "</b>" + (CH.unit ? " " + CH.unit : ""));
        out.innerHTML = "<b>" + CH.categories[st.hl].replace(/</g, "&lt;") + "</b>：" + parts.join("，") + "。";
      }
    }
    if (CH && chartEl) {
      chartEl.addEventListener("mouseover", (e) => {
        const el = e.target.closest && e.target.closest("[data-cat]");
        if (!el) return;
        const c = Number(el.getAttribute("data-cat"));
        if (c >= chartState().reveal) return;
        hover = c; paintChart();
      });
      chartEl.addEventListener("mouseleave", () => { hover = null; paintChart(); });
      chartEl.addEventListener("click", (e) => {
        const el = e.target.closest && e.target.closest("[data-cat]");
        if (!el) return;
        const c = Number(el.getAttribute("data-cat"));
        if (c >= chartState().reveal) return;
        pinned = pinned === c ? null : c;
        hover = null;
        paintChart();
      });
    }
    window.canvasChart = { get pinned() { return pinned; }, state: chartState };

    function go(k) {
      const nk = Math.max(0, Math.min(STEPS.length, k));
      const prevStep = step;
      const forward = nk === step + 1;
      const changed = nk !== step;
      step = nk;
      overviewMode = false;
      smoothCam = true;
      if (nk === 0 && typeof pinned !== "undefined") pinned = null;
      if (!forward) packetAnim++;
      if (changed) document.body.dataset.dir = nk > prevStep ? "fwd" : "back";
      paint(forward);
    }
    // auto-play: one step every interval; manual next/prev/reset pauses it
    const PB = DATA.playback || { autoplay: false, interval_ms: 1800, loop: false };
    let playTimer = null;
    const playBtn = document.getElementById("play");
    function syncPlayBtn() {
      const on = playTimer !== null;
      playBtn.textContent = on ? "❚❚ 暂停" : "▶ 播放";
      playBtn.setAttribute("aria-pressed", on ? "true" : "false");
      document.body.dataset.playing = on ? "1" : "0";
      playBtn.disabled = !STEPS.length;
    }
    function tick() {
      if (step >= STEPS.length) {
        if (PB.loop) { go(0); return; }
        pause(); return;
      }
      go(step + 1);
      if (step >= STEPS.length && !PB.loop) pause();
    }
    function play() {
      if (playTimer !== null || !STEPS.length) return;
      if (step >= STEPS.length) go(0);
      playTimer = setInterval(tick, PB.interval_ms);
      syncPlayBtn();
      setTimeout(() => { if (playTimer !== null && step === 0) tick(); }, 250);
    }
    function pause() {
      if (playTimer !== null) clearInterval(playTimer);
      playTimer = null;
      syncPlayBtn();
    }
    const togglePlay = () => (playTimer === null ? play() : pause());
    const next = () => { pause(); go(step + 1); };
    const prev = () => { pause(); go(step - 1); };
    const reset = () => { pause(); go(0); };
    window.canvasPlayer = { next, prev, reset, play, pause, go, get playing() { return playTimer !== null; },
                            get interval() { return PB.interval_ms; }, get step() { return step; }, get total() { return STEPS.length; } };

    function hook() {
      const svg = host.querySelector("svg");
      if (!svg) return;
      svg.addEventListener("click", (event) => {
        const t = event.target;
        const node = t.closest && t.closest("g.node[data-node-id]");
        if (node && node.classList.contains("cv-current")) { next(); return; }
        const edge = t.closest && t.closest("[data-edge-key]");
        if (edge && edge.classList.contains("cv-next")) { next(); return; }
      });
    }

    function placeClusterLabels() {
      host.querySelectorAll("g.cluster").forEach((c) => {
        const r = c.querySelector("rect");
        const lab = c.querySelector(".cluster-label");
        if (!r || !lab) return;
        const x = Number(r.getAttribute("x")), y = Number(r.getAttribute("y"));
        if (!Number.isFinite(x) || !Number.isFinite(y)) return;
        lab.setAttribute("transform", "translate(" + (x + 12) + ", " + (y + 6) + ")");
        const id = (c.id || "").replace(/^.*grp_/, "");
        c.classList.add("cv-group");
        if (id) c.setAttribute("data-group-id", id);
      });
    }

    function paintGroups(s) {
      host.querySelectorAll(".cv-group[data-members]").forEach((g) => {
        const members = (g.getAttribute("data-members") || "").split(" ");
        const on = members.some((m) => s.current.has(m));
        g.classList.toggle("cv-group-on", on);
        g.classList.toggle("cv-group-off", step > 0 && !on);
        const under = host.querySelector('.cv-group-under[data-group-id="' + CSS.escape(g.getAttribute("data-group-id") || "") + '"]');
        if (under) under.classList.toggle("cv-group-off", step > 0 && !on);
      });
    }

    function syncInlineStyles() {
      const dark = VIDEO;
      host.querySelectorAll("g.node [style], g.node[style]").forEach((el) => {
        if (dark) {
          if (!el.hasAttribute("data-cv-style")) el.setAttribute("data-cv-style", el.getAttribute("style"));
          el.removeAttribute("style");
        }
      });
      if (!dark) host.querySelectorAll("[data-cv-style]").forEach((el) => {
        el.setAttribute("style", el.getAttribute("data-cv-style"));
        el.removeAttribute("data-cv-style");
      });
    }

    function fitEdgeLabels() {
      host.querySelectorAll(".edgeLabel").forEach(g => {
        const t = g.querySelector(".cv-edge-text"), r = g.querySelector(".cv-label-bg");
        if (!t || !r) return;
        let w = 0; try { w = t.getComputedTextLength(); } catch (e) { return; }
        if (!w) return;
        const x0 = parseFloat(r.getAttribute("x")), tx = parseFloat(t.getAttribute("x"));
        r.setAttribute("width", (tx - x0 + w + 9).toFixed(1));
      });
    }

    // mermaid's handDrawn look draws g.rough-node instead of g.node; give them the class the player reads.
    // Pin each rough path's own colours inline first, or mermaid's ".node path" rule would repaint the fill strokes.
    function normalizeRough(root) {
      root.querySelectorAll("g.rough-node").forEach((g) => {
        g.querySelectorAll("path").forEach((p) => {
          ["stroke", "stroke-width", "fill"].forEach((a) => {
            const v = p.getAttribute(a);
            if (v !== null) p.style.setProperty(a, v);
          });
        });
        g.classList.add("node");
      });
    }

    function mount(svg, fromMermaid) {
      host.innerHTML = svg;
      if (fromMermaid) normalizeRough(host);
      fullBox = null;
      prevLit = new Set();
      if (fromMermaid) { tagMermaid(); placeClusterLabels(); } else { fitEdgeLabels(); }
      addHitPaths();
      hook();
      syncInlineStyles();
      paint(false);
    }

    document.getElementById("next").addEventListener("click", next);
    document.getElementById("prev").addEventListener("click", prev);
    document.getElementById("reset").addEventListener("click", reset);
    playBtn.addEventListener("click", (e) => { togglePlay(); e.currentTarget.blur(); });
    syncPlayBtn();
    const qs = new URLSearchParams(location.search);
    if (qs.has("autoplay") ? qs.get("autoplay") !== "0" : PB.autoplay) setTimeout(play, 300);
    document.addEventListener("keydown", (e) => {
      if (e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
      if (document.querySelector("dialog.cv-zoom[open]")) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if ((e.key === " " || e.key === "Enter") && e.target && e.target.closest && e.target.closest("button, a[href]")) return;
      if (e.key === " ") { e.preventDefault(); togglePlay(); }
      else if (e.key === "ArrowRight") { e.preventDefault(); next(); }
      else if (e.key === "ArrowLeft") prev();
      else if (e.key === "Home" || e.key === "r") reset();
    });

    /* ---- toolbar: theme + view pills; the choice is kept in the URL so a reload keeps it ---- */
    function remember(key, value) {
      try {
        const u = new URL(location.href);
        u.searchParams.set(key, value);
        history.replaceState(null, "", u);
      } catch (e) {}
    }
    const themePills = [...document.querySelectorAll("[data-theme-pick]")];
    function syncThemePills() {
      themePills.forEach((b) => b.setAttribute("aria-pressed", b.dataset.themePick === document.body.dataset.theme ? "true" : "false"));
      syncViewPills();
    }
    function syncViewPills() {
      const nav = document.getElementById("views");
      if (!nav || !DATA.boards) return;
      nav.hidden = false;
      nav.querySelectorAll("[data-view]").forEach((a) => {
        const v = a.dataset.view;
        a.hidden = (v === "lesson") !== (DATA.display === "lesson") && !DATA.served;
        const u = new URL(location.href);
        u.searchParams.set("display", v);
        u.searchParams.set("theme", document.body.dataset.theme);
        a.href = u.pathname + u.search;
        if (v === DATA.display) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
      });
    }
    document.getElementById("views").addEventListener("click", (e) => {
      const a = e.target.closest && e.target.closest("[data-view]");
      if (!a) return;
      const v = a.dataset.view;
      if (v === "lesson" || DATA.display === "lesson") return;  // a real reload: the step list differs
      e.preventDefault();
      if (v === DATA.display) return;
      DATA.display = v;
      document.body.setAttribute("data-display", v);
      remember("display", v);
      syncViewPills();
      paint(false);
    });
    themePills.forEach((b) => b.addEventListener("click", () => {
      if (document.body.dataset.theme === b.dataset.themePick) return;
      document.body.dataset.theme = b.dataset.themePick;
      remember("theme", b.dataset.themePick);
      syncThemePills();
      overviewMode = false; syncInlineStyles(); paint(false);
      document.dispatchEvent(new CustomEvent("cv-theme"));
    }));
    // boards: intra click — row / bar / code line / list item; head jumps to board start
    function goIntra(gid, kind, index, block) {
      const idx = STEPS.findIndex((st) => st.group === gid && st.intra
        && st.intra.kind === kind
        && (block == null || st.intra.block === block)
        && st.intra.i === index);
      if (idx >= 0) { pause(); go(idx + 1); return true; }
      const fallback = STEPS.findIndex((st) => st.group === gid);
      if (fallback >= 0) { pause(); go(fallback + 1); return true; }
      return false;
    }
    host.addEventListener("click", (e) => {
      if (!DATA.boards) return;
      const tr = e.target.closest(".cv-tr");
      if (tr) {
        const board = tr.closest(".cv-board");
        const blk = tr.closest(".cv-block");
        goIntra(board.getAttribute("data-group-id"), "table", Number(tr.getAttribute("data-row")), Number(blk.getAttribute("data-block")));
        e.stopPropagation(); return;
      }
      const bar = e.target.closest(".cv-bar-row");
      if (bar) {
        const board = bar.closest(".cv-board");
        const blk = bar.closest(".cv-block");
        goIntra(board.getAttribute("data-group-id"), "bars", Number(bar.getAttribute("data-bar")), Number(blk.getAttribute("data-block")));
        e.stopPropagation(); return;
      }
      const line = e.target.closest(".cv-code-line");
      if (line) {
        const board = line.closest(".cv-board");
        const blk = line.closest(".cv-block");
        goIntra(board.getAttribute("data-group-id"), "code", Number(line.getAttribute("data-line")), Number(blk.getAttribute("data-block")));
        e.stopPropagation(); return;
      }
      const li = e.target.closest(".cv-li");
      if (li) {
        const board = li.closest(".cv-board");
        const blk = li.closest(".cv-block");
        goIntra(board.getAttribute("data-group-id"), "list", Number(li.getAttribute("data-li")), Number(blk.getAttribute("data-block")));
        e.stopPropagation(); return;
      }
      const head = e.target.closest(".cv-board-head");
      if (head) {
        const board = head.closest(".cv-board");
        const gid = board.getAttribute("data-group-id");
        const idx = STEPS.findIndex((st) => st.group === gid);
        if (idx >= 0) { pause(); go(idx + 1); }
      }
    });
    host.addEventListener("mouseover", (e) => {
      if (!DATA.boards) return;
      host.querySelectorAll(".cv-tr-hover").forEach((x) => x.classList.remove("cv-tr-hover"));
      const tr = e.target.closest(".cv-tr");
      if (tr) tr.classList.add("cv-tr-hover");
    });

    const stageEl = document.getElementById("stage");
    if (VIDEO) stageEl.after(document.getElementById("caption"));
    if (DATA.wide) stageEl.classList.add("wide");
    if (DATA.sections) { stageEl.classList.add("sections"); document.body.dataset.layout = "sections"; }
    if (!DATA.hasDiagram) stageEl.classList.add("empty");
    mount(DATA.fallback, false);
    const _qs = new URLSearchParams(location.search);
    let _ori = DATA.orientation || "landscape";
    if (_qs.get("orientation") === "portrait" || _qs.get("orientation") === "landscape") _ori = _qs.get("orientation");
    DATA.orientation = _ori;
    document.body.setAttribute("data-orientation", _ori);
    let _disp = DATA.display || "sheet";
    // in the page only sheet <-> deck can change; lesson has its own step list from the server
    if (["deck", "sheet"].includes(_qs.get("display")) && _disp !== "lesson") _disp = _qs.get("display");
    DATA.display = _disp;
    document.body.setAttribute("data-display", _disp);
    const boardsRoot = document.querySelector(".cv-boards");
    const c = (boardsRoot && boardsRoot.getAttribute("data-cols")) || DATA.cols || 3;
    document.documentElement.style.setProperty("--cv-cols", String(c));
    document.body.setAttribute("data-cols", String(c));
    if (boardsRoot) boardsRoot.setAttribute("data-cols", String(c));
    if (DATA.boards) document.body.setAttribute("data-renderer", "boards");
    else if (!DATA.useMermaid) document.body.setAttribute("data-renderer", DATA.hasDiagram ? "lanes" : "chart");
    if (DATA.display === "lesson") {
      // a board title that only repeats its step caption is noise
      document.querySelectorAll(".cv-lesson-panel").forEach((board) => {
        const st = STEPS.find((x) => x.group === board.getAttribute("data-group-id"));
        const head = board.querySelector(".cv-board-head");
        const title = ((board.querySelector(".cv-board-title") || {}).textContent || "").trim();
        if (head && st && (st.caption || "").trim() === title) head.hidden = true;
      });
      // step 0 = the outline of the lesson; each line jumps to its step
      const boardsEl = document.querySelector(".cv-lesson-boards");
      if (boardsEl && STEPS.length) {
        const toc = document.createElement("ol");
        toc.className = "cv-lesson-toc";
        STEPS.forEach((st, i) => {
          const li = document.createElement("li");
          li.style.setProperty("--i", String(i));
          const t = document.createElement("span");
          t.className = "toc-title";
          t.textContent = st.caption || "";
          li.appendChild(t);
          li.addEventListener("click", () => { pause(); go(i + 1); });
          toc.appendChild(li);
        });
        boardsEl.insertBefore(toc, boardsEl.firstChild);
      }
    }
    syncThemePills();

    /* ---- drawing frame: coordinate rulers on four sides + a title strip at the bottom ---- */
    function drawFrame() {
      if (VIDEO) return;
      const ownSvg = !DATA.boards && !DATA.useMermaid && DATA.hasDiagram;  // lane / section diagrams from layout.py
      const target = DATA.boards
        ? (DATA.display === "lesson" ? null : document.querySelector(".cv-sheet"))
        : (ownSvg ? stageEl : null);
      if (!target) return;
      target.classList.add("cv-drawing");
      if (ownSvg) {
        // the page column hugs the drawing: wide diagrams widen it, narrow ones keep the reading width
        const svg = host.querySelector("svg");
        const vb = svg && svg.viewBox && svg.viewBox.baseVal;
        if (vb && vb.width) document.querySelector(".frame").style.width = "min(97vw, " + Math.max(736, Math.round(vb.width * 1.05 + 96)) + "px)";
      }
      const ruler = (side, labels) => {
        const r = document.createElement("div");
        r.className = "cv-ruler cv-ruler-" + side;
        r.setAttribute("aria-hidden", "true");
        labels.forEach((t) => { const sp = document.createElement("span"); sp.textContent = t; r.appendChild(sp); });
        target.appendChild(r);
      };
      const cols = ["1", "2", "3", "4", "5", "6", "7", "8"];
      const rows = ["A", "B", "C", "D"];
      ruler("top", cols); ruler("bottom", cols); ruler("left", rows); ruler("right", rows);
      const cells = [["标题", DATA.title || ""]].concat(Object.entries(DATA.meta || {}));
      cells.push(["步数", String(STEPS.length)]);
      const strip = document.createElement("footer");
      strip.className = "cv-titleblock";
      cells.forEach(([k, v], i) => {
        const cell = document.createElement("div");
        cell.className = "cv-tb-cell" + (i === 0 ? " cv-tb-title" : "");
        const key = document.createElement("span");
        key.textContent = k;
        const val = document.createElement("b");
        val.textContent = v;
        cell.append(key, val);
        strip.appendChild(cell);
      });
      target.appendChild(strip);
    }
    drawFrame();

    /* ---- open one board on its own, larger, without losing your place in the sheet ---- */
    let zoomDlg = null;
    function openBoard(board) {
      if (!zoomDlg) {
        zoomDlg = document.createElement("dialog");
        zoomDlg.className = "cv-zoom";
        zoomDlg.addEventListener("click", (e) => { if (e.target === zoomDlg) zoomDlg.close(); });
        zoomDlg.addEventListener("close", () => {
          zoomDlg.replaceChildren();
          document.documentElement.classList.remove("cv-zoom-open");
        });
        document.body.appendChild(zoomDlg);
      }
      const copy = board.cloneNode(true);
      copy.classList.remove("cv-dim", "cv-board-enter");
      copy.classList.add("cv-board-zoomed");
      copy.querySelectorAll("*").forEach((el) => {
        [...el.classList].forEach((k) => { if (/-pending$|^cv-md-(node|edge|msg)-dim$/.test(k)) el.classList.remove(k); });
      });
      copy.querySelectorAll(".cv-bar-row").forEach((row) => {
        const fill = row.querySelector(".cv-bar-fill");
        if (fill) { fill.style.transition = "none"; fill.style.width = (row.getAttribute("data-pct") || "0") + "%"; }
        const needle = row.querySelector(".cv-bar-needle");
        if (needle) needle.style.left = (row.getAttribute("data-pct") || "0") + "%";
      });
      const btn = copy.querySelector(".cv-board-zoom");
      if (btn) { btn.textContent = "×"; btn.title = "关闭（Esc）"; btn.setAttribute("aria-label", "关闭"); btn.onclick = () => zoomDlg.close(); }
      zoomDlg.replaceChildren(copy);
      document.documentElement.classList.add("cv-zoom-open");
      zoomDlg.showModal();
    }
    host.addEventListener("click", (e) => {
      const z = e.target.closest && e.target.closest(".cv-board-zoom");
      if (!z) return;
      e.stopPropagation();
      openBoard(z.closest(".cv-board"));
    }, true);

    function fitBoardMermaidLabels(root) {
      /* Expand foreignObject / node boxes so labels never clip. Prefer wrap + grow. */
      const scope = root || host;
      scope.querySelectorAll(".cv-md foreignObject").forEach((fo) => {
        const box = fo.querySelector("div, span, p") || fo.firstElementChild;
        if (!box) return;
        box.style.whiteSpace = "normal";
        box.style.overflow = "visible";
        box.style.textOverflow = "clip";
        box.style.wordBreak = "break-word";
        box.style.lineHeight = "1.35";
        box.style.maxWidth = "none";
        // measure after style settle
        const pad = 10;
        const needW = Math.ceil(Math.max(box.scrollWidth, box.offsetWidth)) + pad;
        const needH = Math.ceil(Math.max(box.scrollHeight, box.offsetHeight)) + 6;
        const curW = Number(fo.getAttribute("width") || 0);
        const curH = Number(fo.getAttribute("height") || 0);
        if (needW > curW) fo.setAttribute("width", String(needW));
        if (needH > curH) fo.setAttribute("height", String(needH));
        const g = fo.closest("g.node, g.actor, g.cluster");
        if (!g || g.classList.contains("rough-node")) return;  // hand-drawn shapes are paths, not resizable boxes
        const shape = g.querySelector("rect, polygon, circle, ellipse");
        if (!shape) return;
        if (shape.tagName.toLowerCase() === "rect") {
          const rw = Number(shape.getAttribute("width") || 0);
          const rh = Number(shape.getAttribute("height") || 0);
          const fox = Number(fo.getAttribute("x") || 0);
          const foy = Number(fo.getAttribute("y") || 0);
          const sx = Number(shape.getAttribute("x") || 0);
          const sy = Number(shape.getAttribute("y") || 0);
          const targetW = Math.max(rw, needW + 12);
          const targetH = Math.max(rh, needH + 10);
          if (targetW > rw) {
            const grow = targetW - rw;
            shape.setAttribute("width", String(targetW));
            shape.setAttribute("x", String(sx - grow / 2));
            fo.setAttribute("x", String(fox - grow / 2));
          }
          if (targetH > rh) {
            const grow = targetH - rh;
            shape.setAttribute("height", String(targetH));
            shape.setAttribute("y", String(sy - grow / 2));
            fo.setAttribute("y", String(foy - grow / 2));
          }
        }
      });
      // sequence actor labels (text nodes)
      scope.querySelectorAll(".cv-md .actor > text, .cv-md text.actor").forEach((t) => {
        try {
          const bb = t.getBBox();
          const parent = t.parentElement;
          const rect = parent && parent.querySelector("rect");
          if (rect) {
            const rw = Number(rect.getAttribute("width") || 0);
            if (bb.width + 12 > rw) {
              const grow = bb.width + 12 - rw;
              rect.setAttribute("width", String(rw + grow));
              const x = Number(rect.getAttribute("x") || 0);
              rect.setAttribute("x", String(x - grow / 2));
            }
          }
        } catch (e) {}
      });
      // bump svg viewBox if content overflows
      scope.querySelectorAll(".cv-md svg").forEach((svg) => {
        try {
          const bb = svg.getBBox();
          const pad = 8;
          const minX = Math.min(0, bb.x - pad);
          const minY = Math.min(0, bb.y - pad);
          const w = Math.max(bb.width + pad * 2, bb.x + bb.width + pad - minX);
          const h = Math.max(bb.height + pad * 2, bb.y + bb.height + pad - minY);
          svg.setAttribute("viewBox", minX + " " + minY + " " + w + " " + h);
          svg.removeAttribute("height");
          svg.style.width = "100%";
          svg.style.height = "auto";
          svg.style.overflow = "visible";
        } catch (e) {}
      });
    }

    function mdSource(el) {
      if (el.dataset.mdSrc) return el.dataset.mdSrc;
      const pre = el.querySelector(".cv-md-src");
      const s = (pre ? pre.textContent : el.textContent) || "";
      el.dataset.mdSrc = s.trim();
      return el.dataset.mdSrc;
    }
    function mdSvgBad(svg) {
      if (!svg) return true;
      try {
        const vb = svg.viewBox && svg.viewBox.baseVal;
        if (vb && (vb.width < 48 || vb.height < 48)) return true;
        const r = svg.getBoundingClientRect();
        if (r.width > 0 && r.height > 0 && (r.width < 40 || r.height < 40)) return true;
      } catch (e) {}
      return false;
    }
    async function renderBoardMermaids(scope, force) {
      if (!window.mermaid) return;
      const root = scope || host;
      const nodes = [...root.querySelectorAll(".cv-md")];
      for (let i = 0; i < nodes.length; i++) {
        const el = nodes[i];
        // skip hidden parents unless forced scope (visible panel re-render)
        if (!scope && el.closest("[hidden]")) continue;
        const src = mdSource(el);
        if (!src) continue;
        const existing = el.querySelector("svg");
        if (!force && existing && !mdSvgBad(existing) && el.dataset.mdReady === "1") continue;
        try {
          const id = "boardMd" + i + "_" + Math.random().toString(36).slice(2, 7);
          const drawn = await window.mermaid.render(id, src);
          const stash = src;
          el.innerHTML = drawn.svg;
          normalizeRough(el);
          el.dataset.mdSrc = stash;
          el.dataset.mdReady = "1";
          const svg = el.querySelector("svg");
          if (svg) {
            // keep the diagram near its natural size: no blowing up, no shrinking into unreadable text
            const vb = svg.viewBox && svg.viewBox.baseVal;
            svg.removeAttribute("height");
            svg.style.width = "100%";
            const k = vb && vb.height ? Math.max(0.7, Math.min(1.12, 460 / vb.height)) : 1.12;
            svg.style.maxWidth = vb && vb.width ? Math.round(vb.width * k) + "px" : "100%";
            svg.style.height = "auto";
            svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
          }
          if (mdSvgBad(svg)) el.dataset.mdReady = "0";
        } catch (err) {
          el.dataset.mdReady = "0";
          el.innerHTML = '<pre class="cv-code">' + src.replace(/&/g,'&amp;').replace(/</g,'&lt;') + '</pre>';
        }
      }
      requestAnimationFrame(() => {
        fitBoardMermaidLabels(root);
        requestAnimationFrame(() => { fitBoardMermaidLabels(root); paint(false); });
      });
    }

    /* ---- theme fonts + mermaid colours follow the page theme ---- */
    const FONT_CSS = {
      sketch: [
        "https://cdn.jsdelivr.net/npm/lxgw-wenkai-webfont@1.7.0/lxgwwenkai-regular.css",
        "https://cdn.jsdelivr.net/npm/lxgw-wenkai-webfont@1.7.0/lxgwwenkai-bold.css"
      ]
    };
    function ensureFonts(theme) {
      const jobs = (FONT_CSS[theme] || []).map((href) => new Promise((done) => {
        const have = document.querySelector('link[data-cv-font="' + href + '"]');
        if (have) { done(); return; }
        const l = document.createElement("link");
        l.rel = "stylesheet"; l.href = href; l.dataset.cvFont = href;
        l.onload = l.onerror = () => done();
        document.head.appendChild(l);
        setTimeout(done, 2500);
      }));
      const faces = theme === "sketch" && document.fonts
        ? () => Promise.all([document.fonts.load('16px "LXGW WenKai"', "中文字"), document.fonts.load("16px Virgil", "Ab1")])
        : () => Promise.resolve();
      return Promise.all(jobs).then(() => Promise.race([
        faces(),
        new Promise((r) => setTimeout(r, 2500))
      ])).catch(() => {});
    }
    function cssVar(name, fallback) {
      const v = getComputedStyle(document.body).getPropertyValue(name).trim();
      return v || fallback;
    }
    function mermaidConfig() {
      const sketch = document.body.dataset.theme === "sketch";
      const ink = cssVar("--ink", "#111111");
      const ink2 = cssVar("--ink-2", ink);
      const surface = cssVar("--surface", "#ffffff");
      const soft = cssVar("--surface-2", surface);
      const stroke = cssVar("--node-stroke", ink);
      const line = cssVar("--line", stroke);
      return {
        startOnLoad: false,
        securityLevel: "strict",
        theme: "base",
        look: sketch ? "handDrawn" : "classic",
        handDrawnSeed: 7,
        themeVariables: {
          fontFamily: cssVar("--font-body", "system-ui, sans-serif"), fontSize: sketch ? "17px" : "15px",
          background: surface, mainBkg: surface, primaryColor: surface, secondaryColor: soft, tertiaryColor: soft,
          primaryTextColor: ink, secondaryTextColor: ink, tertiaryTextColor: ink, textColor: ink,
          nodeTextColor: ink, titleColor: ink, labelTextColor: ink, signalTextColor: ink, actorTextColor: ink,
          primaryBorderColor: stroke, secondaryBorderColor: stroke, tertiaryBorderColor: stroke, nodeBorder: stroke,
          clusterBkg: soft, clusterBorder: line, lineColor: ink2, signalColor: ink2, edgeLabelBackground: surface,
          labelBoxBkgColor: surface, labelBoxBorderColor: stroke, actorBkg: surface, actorBorder: stroke, actorLineColor: line,
          noteBkgColor: cssVar("--accent-soft", soft), noteBorderColor: stroke, noteTextColor: ink
        },
        flowchart: { htmlLabels: true, padding: 14, nodeSpacing: 40, rankSpacing: 50, curve: sketch ? "basis" : "linear",
                     diagramPadding: 8, wrappingWidth: 180, useMaxWidth: true },
        sequence: { useMaxWidth: true, diagramMarginX: 12, diagramMarginY: 12, actorFontSize: 15, messageFontSize: 14, wrap: true }
      };
    }
    async function rethemeMermaid() {
      if (!window.mermaid || !upgraded) return;
      window.mermaid.initialize(mermaidConfig());
      if (DATA.boards) {
        host.querySelectorAll(".cv-md").forEach((el) => { el.dataset.mdReady = "0"; });
        await renderBoardMermaids(null, true);
      } else if (DATA.useMermaid && DATA.mermaid) {
        try {
          const drawn = await window.mermaid.render("canvasGraph" + Date.now().toString(36), DATA.mermaid);
          mount(drawn.svg, true);
        } catch (err) {}
      }
    }
    document.addEventListener("cv-theme", async () => {
      await ensureFonts(document.body.dataset.theme);
      rethemeMermaid();
    });
    const fontsFirst = ensureFonts(document.body.dataset.theme);

    const loader = document.createElement("script");
    loader.src = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js";
    loader.onload = async () => {
      if (!window.mermaid || upgraded) return;
      try {
        await fontsFirst;
        window.mermaid.initialize(mermaidConfig());
        if (DATA.boards) {
          upgraded = true;
          await renderBoardMermaids();
          return;
        }
        const drawn = await window.mermaid.render("canvasGraph", DATA.mermaid);
        upgraded = true;
        mount(drawn.svg, true);
        document.body.setAttribute("data-renderer", "mermaid");
      } catch (err) {
        upgraded = false;
        document.body.setAttribute("data-renderer", DATA.boards ? "boards" : "fallback");
      }
    };
    loader.onerror = () => document.body.setAttribute("data-renderer", DATA.boards ? "boards" : "fallback");
    if (DATA.useMermaid || DATA.boards) document.head.appendChild(loader);
  </script>
<!--VIDEO-->
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    """GET only. No POST route: canvas never writes back."""

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("/", "/video"):
            self.send_error(404)
            return
        query = parse_qs(parsed.query)
        src = query.get("src", [None])[0]
        theme = query.get("theme", [None])[0]
        display = query.get("display", [None])[0]
        orientation = query.get("orientation", [None])[0]
        try:
            page = render_page(resolve_src(src), theme, "video" if parsed.path == "/video" else "page", display,
                               orientation, served=True)
        except (ValueError, KeyError) as exc:
            body = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def make_server(host: str = HOST, port: int = PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def main():
    httpd = make_server()
    print(f"http://{HOST}:{PORT}/?src=tcp-handshake.content.json")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
