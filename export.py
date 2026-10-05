#!/usr/bin/env python3
"""Export canvas-content/v0 to other targets.

    python3 export.py SRC.content.json --to obsidian   [-o out.canvas]
    python3 export.py SRC.content.json --to excalidraw [-o out.excalidraw] [--theme sketch]
    python3 export.py SRC.content.json --to html       [-o out.html]       [--theme book|product|sketch]
    python3 export.py SRC.content.json --to mermaid

Coordinates come from layout.py. The content file is only read, never written.
Obsidian .canvas follows JSON Canvas 1.0 (jsoncanvas.org, MIT).
Excalidraw output is a full scene file (type "excalidraw", version 2) that excalidraw.com
and the Obsidian Excalidraw plugin can open.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import layout as lay
import boards
import server

# product role -> JSON Canvas preset colour ("1" red .. "6" purple)
OBSIDIAN_COLOR = {"keep": "4", "reset": "1", "human": "1", "agent": "6", "editable": "5", "decision": "3"}
# product role -> (background, stroke) for Excalidraw
EXCALI_COLOR = {
    "agent": ("#111111", "#111111", "#ffffff"),
    "keep": ("#dcfce7", "#16a34a", "#166534"),
    "reset": ("#fee2e2", "#dc2626", "#991b1b"),
    "human": ("#fee2e2", "#dc2626", "#991b1b"),
    "editable": ("#f3f4f6", "#9ca3af", "#374151"),
}
THEME_BG = {"book": "#f6f2ea", "product": "#f5f6f8", "sketch": "#f2e7cc"}


def _sid(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


def _seed(*parts) -> int:
    return int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:7], 16)


def _edges(data):
    return lay._edges(data)


def chart_markdown(chart: dict) -> str:
    head = "| " + " | ".join([""] + [s["name"] for s in chart["series"]]) + " |"
    sep = "|" + " --- |" * (len(chart["series"]) + 1)
    rows = []
    for i, c in enumerate(chart["categories"]):
        vals = ["" if s["values"][i] is None else f"{s['values'][i]:g}" for s in chart["series"]]
        rows.append("| " + " | ".join([c] + vals) + " |")
    title = chart["title"] or "chart"
    tag = "（示例数据）" if chart["sample"] else ""
    lines = [f"**{title}**{tag}", f"_{chart['type']} chart" + (f", unit: {chart['unit']}" if chart["unit"] else "") + "_", "", head, sep, *rows]
    if chart["source"]:
        lines += ["", chart["source"]]
    return "\n".join(lines)


def _below(L: dict) -> int:
    return (L["height"] + 20) if L["nodes"] else 0


def to_obsidian(data: dict) -> dict:
    L = lay.layout(data)
    boxes = L["nodes"]
    nodes = []
    for g in L["groups"]:
        if not g["title"]:
            continue
        label = (f"{g['letter']} " if g.get("letter") else "") + g["title"] + (f" · {g['subtitle']}" if g["subtitle"] else "")
        item = {"id": f"group-{g['id']}", "type": "group", "label": label,
                "x": g["x"], "y": g["y"], "width": g["w"], "height": g["h"] + 16}
        color = OBSIDIAN_COLOR.get(g["role"])
        if color:
            item["color"] = color
        nodes.append(item)
    for n in data.get("nodes") or []:
        nid = str(n.get("id") or "")
        if nid not in boxes:
            continue
        x, y, w, h = boxes[nid]
        lines = lay.label_lines(n.get("label") or nid)
        text = f"**{lines[0]}**" + ("".join("\n" + l for l in lines[1:]))
        item = {"id": nid, "type": "text", "text": text, "x": x, "y": y, "width": w, "height": h + 16}
        color = OBSIDIAN_COLOR.get(str(n.get("role") or ""))
        if color:
            item["color"] = color
        nodes.append(item)
    edges = []
    keys = server.edge_keys([(str(e["from"]), str(e["to"])) for e in _edges(data)])
    for key, e in zip(keys, _edges(data)):
        left, right = str(e["from"]), str(e["to"])
        if str(e.get("style") or "").lower() == "invisible" or left not in boxes or right not in boxes:
            continue
        pts = lay.route(L, left, right)
        item = {"id": key, "fromNode": left, "toNode": right,
                "fromSide": lay.side(boxes[left], pts[0]), "toSide": lay.side(boxes[right], pts[-1]),
                "toEnd": "arrow"}
        if e.get("label"):
            item["label"] = str(e["label"])
        edges.append(item)
    if data.get("chart"):
        rows = len(data["chart"]["categories"])
        nodes.append({"id": "chart", "type": "text", "text": chart_markdown(data["chart"]),
                      "x": 24, "y": _below(L), "width": 160 + 110 * len(data["chart"]["series"]),
                      "height": 150 + 34 * rows})
    return {"nodes": nodes, "edges": edges}


def _base(kind: str, eid: str, x, y, w, h, **extra) -> dict:
    el = {
        "id": eid, "type": kind, "x": x, "y": y, "width": w, "height": h, "angle": 0,
        "strokeColor": "#1e1e1e", "backgroundColor": "transparent", "fillStyle": "solid",
        "strokeWidth": 1, "strokeStyle": "solid", "roughness": 0, "opacity": 100,
        "groupIds": [], "frameId": None, "roundness": None, "seed": _seed(eid),
        "version": 1, "versionNonce": _seed(eid, "n"), "isDeleted": False,
        "boundElements": [], "updated": 1, "link": None, "locked": False,
    }
    el.update(extra)
    return el


def _text(eid: str, text: str, x, y, w, h, container=None, size=16, color="#1e1e1e", italic=False) -> dict:
    return _base("text", eid, x, y, w, h, strokeColor=color, text=text, originalText=text,
                 fontSize=size, fontFamily=5, textAlign="center", verticalAlign="middle",
                 containerId=container, lineHeight=1.25, autoResize=True)


def to_excalidraw(data: dict, theme: str = "book") -> dict:
    L = lay.layout(data)
    boxes = L["nodes"]
    els = []
    frame_of = {}
    frames = []
    for g in L["groups"]:
        if not g["title"]:
            continue
        fid = _sid("frame", g["id"])
        name = (f"{g['letter']} " if g.get("letter") else "") + g["title"] + (f" · {g['subtitle']}" if g["subtitle"] else "")
        frames.append(_base("frame", fid, g["x"], g["y"], g["w"], g["h"], name=name, strokeColor="#bbb"))
        for m in g["members"]:
            frame_of[m] = fid
    node_el = {}
    for n in data.get("nodes") or []:
        nid = str(n.get("id") or "")
        if nid not in boxes:
            continue
        x, y, w, h = boxes[nid]
        role = str(n.get("role") or "")
        bg, stroke, ink = EXCALI_COLOR.get(role, ("transparent" if theme == "sketch" else "#ffffff", "#1e1e1e", "#1e1e1e"))
        shape = str(n.get("shape") or "rect").lower()
        kind = {"diamond": "diamond", "ellipse": "ellipse", "circle": "ellipse"}.get(shape, "rectangle")
        rid = _sid("node", nid)
        tid = _sid("node-t", nid)
        rect = _base(kind, rid, x, y, w, h, backgroundColor=bg, strokeColor=stroke, frameId=frame_of.get(nid),
                     strokeStyle="dashed" if role == "dashed" else "solid",
                     roundness={"type": 3} if kind == "rectangle" else None,
                     boundElements=[{"type": "text", "id": tid}], customData={"canvasId": nid})
        label = str(n.get("label") or nid)
        lines = lay.label_lines(label)
        th = len(lines) * 20
        els.append(rect)
        txt = _text(tid, label, x + 6, y + (h - th) / 2, w - 12, th, container=rid, color=ink)
        txt["frameId"] = frame_of.get(nid)
        els.append(txt)
        node_el[nid] = rect
    keys = server.edge_keys([(str(e["from"]), str(e["to"])) for e in _edges(data)])
    for key, e in zip(keys, _edges(data)):
        left, right = str(e["from"]), str(e["to"])
        if str(e.get("style") or "").lower() == "invisible" or left not in boxes or right not in boxes:
            continue
        pts = lay.route(L, left, right)
        x1, y1 = pts[0]
        rel = [[px - x1, py - y1] for px, py in pts]
        xs, ys = [p[0] for p in rel], [p[1] for p in rel]
        aid = _sid("edge", key)
        arrow = _base("arrow", aid, x1, y1, max(xs) - min(xs), max(ys) - min(ys),
                      strokeStyle="dashed" if str(e.get("style") or "") == "dashed" else "solid",
                      points=rel, lastCommittedPoint=None,
                      startBinding={"elementId": node_el[left]["id"], "focus": 0, "gap": 4},
                      endBinding={"elementId": node_el[right]["id"], "focus": 0, "gap": 4},
                      startArrowhead=None, endArrowhead="arrow", elbowed=False,
                      roundness=None, customData={"canvasEdge": key})
        node_el[left]["boundElements"].append({"type": "arrow", "id": aid})
        node_el[right]["boundElements"].append({"type": "arrow", "id": aid})
        els.append(arrow)
        if e.get("label"):
            lid = _sid("edge-t", key)
            text = str(e["label"])
            tw = lay.text_px(text) * 0.9 + 10
            arrow["boundElements"].append({"type": "text", "id": lid})
            mx, my = lay.label_point(pts)
            els.append(_text(lid, text, mx - tw / 2, my - 10, tw, 20, container=aid, size=14))
    if data.get("chart"):
        text = chart_markdown(data["chart"]).replace("**", "").replace("_", "")
        n = text.count("\n") + 1
        els.append(_base("text", _sid("chart"), 24, _below(L), 420, n * 22, text=text, originalText=text,
                         fontSize=16, fontFamily=3, textAlign="left", verticalAlign="top",
                         containerId=None, lineHeight=1.25, autoResize=True))
    els = frames + els  # frames first; children point at them via frameId
    # sketch keeps Excalidraw's own hand-drawn look; book / product draw clean lines in a plain font
    for el in els:
        if theme == "sketch":
            if el["type"] in {"rectangle", "diamond", "ellipse", "arrow"}:
                el["roughness"], el["strokeWidth"] = 1, 2
        elif el["type"] == "text" and el.get("fontFamily") == 5:
            el["fontFamily"] = 6
    app = {"viewBackgroundColor": THEME_BG.get(theme, "#ffffff"), "gridSize": 20}
    return {"type": "excalidraw", "version": 2, "source": "canvas/export.py", "elements": els,
            "appState": app, "files": {}}


def to_video(src: Path, out: Path, fps: int = 30, width: int = 1920, height: int = 1080, log=print) -> Path:
    """3b1b-style mp4: headless Chromium renders /video frame by frame (cvVideo.render(t)), ffmpeg encodes.

    Deterministic: every frame is painted from t alone, no wall-clock animation.
    """
    import shutil
    import subprocess
    import threading

    src = server.resolve_src(str(Path(src).resolve()))
    if server.is_content_json(src) and boards.has_boards(server.load_content_json(src)):
        raise RuntimeError("video export draws node diagrams; teaching-board sheets have none (use --to html)")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found")
    from playwright.sync_api import sync_playwright
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    httpd = server.make_server("127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/video?src={server.rel_src(src)}"
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            pg.goto(url)
            pg.wait_for_selector("body[data-renderer]", timeout=30000)
            pg.evaluate("document.fonts.ready.then(() => true)")
            pg.wait_for_timeout(300)
            if not pg.evaluate("window.cvVideo && window.cvVideo.init()"):
                raise RuntimeError("video renderer did not initialise (no diagram?)")
            duration = float(pg.evaluate("window.cvVideo.duration"))
            frames = int(round(duration * fps))
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "png", "-i", "-",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "medium",
                   "-r", str(fps), "-movflags", "+faststart", str(out)]
            enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            try:
                for i in range(frames):
                    pg.evaluate("t => window.cvVideo.render(t)", i / fps)
                    enc.stdin.write(pg.screenshot(type="png"))
                    if log and i % (fps * 5) == 0:
                        log(f"  frame {i}/{frames}")
            finally:
                enc.stdin.close()
                enc.wait()
            b.close()
            if enc.returncode:
                raise RuntimeError(f"ffmpeg exited {enc.returncode}")
    finally:
        httpd.shutdown()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("--to", required=True, choices=["obsidian", "excalidraw", "html", "mermaid", "video"])
    ap.add_argument("-o", "--out")
    ap.add_argument("--theme", choices=list(server.THEMES), help="page / export theme; video always uses its own 3b1b look")
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args(argv)
    src = Path(args.src)
    data = server.load_content_json(src)
    theme = server.pick_theme(args.theme, data.get("theme"))
    stem = src.name.replace(".content.json", "").replace(".json", "")
    if args.to == "video":
        out = Path(args.out) if args.out else Path("out") / (stem + ".mp4")
        print(to_video(src, out, fps=args.fps))
        return 0
    if args.to == "obsidian":
        text, suffix = json.dumps(to_obsidian(data), ensure_ascii=False, indent=2), ".canvas"
    elif args.to == "excalidraw":
        text, suffix = json.dumps(to_excalidraw(data, theme), ensure_ascii=False, indent=2), ".excalidraw"
    elif args.to == "html":
        text, suffix = server.render_json_page(src.resolve(), theme), f".{theme}.html"
    else:
        text, suffix = server.content_to_mermaid(data), ".mmd"
    out = Path(args.out) if args.out else Path("exports") / (stem + suffix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
    print(out)
    return 0


def cli(argv=None) -> int:
    """main() with content mistakes reported as one line (exit 2) instead of a traceback."""
    try:
        return main(argv)
    except FileNotFoundError as exc:
        print(f"找不到文件：{exc.filename}", file=sys.stderr)
    except (ValueError, KeyError, TypeError) as exc:
        print(f"内容有误：{exc}", file=sys.stderr)
    except RuntimeError as exc:
        print(f"导出失败：{exc}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(cli())
