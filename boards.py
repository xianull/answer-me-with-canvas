#!/usr/bin/env python3
"""Teaching-board blocks inside groups (answer-me / mnemon style).

A group may carry `blocks` (short `b`): callout, table, bars, flowchart,
sequence, code, mermaid, list. Boards mode renders dense HTML panels A–J
instead of a sparse node graph.
"""
from __future__ import annotations

import math
import re

from html import escape as html_escape
from typing import Any

BLOCK_KINDS = {
    "callout", "co", "table", "tb", "bars", "bar", "flowchart", "flow",
    "sequence", "seq", "code", "mermaid", "md", "list", "ul", "text", "p",
    "chips", "chip", "tokens", "tok",
}

def _letter_for(i: int) -> str:
    s = ""
    n = i + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


KIND_ALIAS = {
    "co": "callout", "tb": "table", "bar": "bars", "flow": "flowchart",
    "seq": "sequence", "md": "mermaid", "ul": "list", "p": "text",
    "chip": "chips", "tokens": "chips", "tok": "chips",
}


def has_boards(data: dict) -> bool:
    for g in data.get("groups") or []:
        if isinstance(g, dict) and (g.get("blocks") or g.get("b")):
            return True
    return False


def expand_group_blocks(g: dict) -> list[dict]:
    raw = g.get("blocks")
    if raw is None and "b" in g:
        raw = g.pop("b")
        g["blocks"] = raw
    if not isinstance(raw, list):
        g["blocks"] = []
        return []
    out = []
    for item in raw:
        b = expand_block(item)
        if b:
            out.append(b)
    g["blocks"] = out
    return out


def expand_block(item: Any) -> dict | None:
    if isinstance(item, str):
        return {"type": "callout", "text": item}
    if not isinstance(item, dict):
        return None
    # short keys
    if "k" in item and "type" not in item:
        item["type"] = item.pop("k")
    if "t" in item and "text" not in item and item.get("type") in {None, "callout", "co", "code", "text", "p"}:
        item["text"] = item.pop("t")
    if "m" in item and "mermaid" not in item:
        item["mermaid"] = item.pop("m")
    if "h" in item and "headers" not in item:
        item["headers"] = item.pop("h")
    if "r" in item and "rows" not in item:
        item["rows"] = item.pop("r")
    if "i" in item and "items" not in item:
        item["items"] = item.pop("i")
    if "lg" in item and "lang" not in item:
        item["lang"] = item.pop("lg")
    if "o" in item and "tone" not in item:
        item["tone"] = item.pop("o")

    kind = str(item.get("type") or "callout").lower()
    kind = KIND_ALIAS.get(kind, kind)
    if kind not in {"callout", "table", "bars", "flowchart", "sequence", "code", "mermaid", "list", "text", "chips"}:
        kind = "callout"
    item["type"] = kind

    if kind == "callout":
        item.setdefault("text", "")
        item.setdefault("tone", "info")
    elif kind == "table":
        item.setdefault("headers", [])
        item.setdefault("rows", [])
    elif kind == "chips":
        items = []
        for it in item.get("items") or []:
            if isinstance(it, str):
                items.append({"label": it, "sub": ""})
            elif isinstance(it, dict):
                if "l" in it and "label" not in it:
                    it["label"] = it.pop("l")
                if "s" in it and "sub" not in it:
                    it["sub"] = it.pop("s")
                if "sub" not in it and "idx" in it:
                    it["sub"] = it.pop("idx")
                items.append({
                    "label": str(it.get("label") or ""),
                    "sub": str(it.get("sub") or ""),
                })
        item["items"] = items
    elif kind == "bars":
        items = []
        for it in item.get("items") or []:
            if isinstance(it, dict):
                if "l" in it and "label" not in it:
                    it["label"] = it.pop("l")
                if "v" in it and "value" not in it:
                    it["value"] = it.pop("v")
                if "m" in it and "max" not in it:
                    it["max"] = it.pop("m")
                lab = str(it.get("label") or "")
                items.append({
                    "label": lab,
                    "value": _number(it.get("value") or 0, f"bars 里「{lab}」的 v"),
                    "max": _number(it.get("max") or 1, f"bars 里「{lab}」的 m") or 1,
                    "unit": str(it.get("unit") or it.get("u") or ""),
                })
        item["items"] = items
    elif kind in {"flowchart", "sequence", "mermaid"}:
        src = str(item.get("mermaid") or item.get("text") or "").strip()
        if kind == "flowchart" and src and not src.lower().startswith(("flowchart", "graph")):
            src = "flowchart LR\n" + src
        if kind == "sequence" and src and not src.lower().startswith("sequencediagram"):
            src = "sequenceDiagram\n" + src
        item["mermaid"] = src
        item["type"] = "mermaid" if kind == "mermaid" else kind
    elif kind == "code":
        item.setdefault("text", "")
        item.setdefault("lang", "")
    elif kind == "list":
        items = []
        for it in item.get("items") or []:
            items.append(str(it))
        item["items"] = items
    elif kind == "text":
        item.setdefault("text", "")
    return item


def _number(v: Any, what: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} 要写数字，现在是 {v!r}") from None


def expand_board_steps(data: dict) -> None:
    """Turn {g,c} / {group,caption} steps into board reveal steps; default 1/group."""
    groups = [g for g in (data.get("groups") or []) if isinstance(g, dict) and g.get("id")]
    if not groups:
        return
    ids = [str(g["id"]) for g in groups]
    raw = data.get("steps")
    out = []
    if not isinstance(raw, list) or not raw:
        for i, g in enumerate(groups):
            out.append({
                "group": g["id"],
                "caption": str(g.get("title") or g["id"]),
                "reveal_groups": ids[: i + 1],
                "edges": [],
                "focus": [],
                "optional": False,
            })
    else:
        for item in raw:
            if isinstance(item, str):
                # "a|caption" or just "a"
                gid, _, cap = item.partition("|")
                gid = gid.strip()
                if gid in ids:
                    out.append(_board_step(gid, cap.strip() or next(g["title"] for g in groups if g["id"] == gid), ids))
                continue
            if not isinstance(item, dict):
                continue
            if "c" in item and "caption" not in item:
                item["caption"] = item.pop("c")
            gid = item.get("group") or item.get("g")
            if gid and "from" not in item and "edge" not in item:
                out.append(_board_step(str(gid), str(item.get("caption") or ""), ids))
            elif item.get("reveal_groups"):
                rg = [str(x) for x in item["reveal_groups"]]
                out.append({
                    "group": rg[-1] if rg else ids[0],
                    "caption": str(item.get("caption") or ""),
                    "reveal_groups": rg,
                    "edges": [],
                    "focus": [],
                    "optional": bool(item.get("optional")),
                })
            # else leave for edge-based expand (mixed docs)
            elif "from" in item or "edge" in item or "f" in item:
                out.append(item)
        # if nothing board-like produced but groups have blocks, synthesize
        if out and all(isinstance(x, dict) and x.get("reveal_groups") is not None for x in out):
            pass
        elif not any(isinstance(x, dict) and x.get("reveal_groups") for x in out):
            # steps were edge-form; keep them — caller may not be boards-only
            data["steps"] = out if out else raw
            return
    data["steps"] = out


def _board_step(gid: str, caption: str, ids: list[str]) -> dict:
    if gid not in ids:
        raise ValueError(f"步骤指向不存在的板 {gid!r}，现有的板是 {', '.join(ids)}")
    i = ids.index(gid)
    title = caption
    return {
        "group": gid,
        "caption": title,
        "reveal_groups": ids[: i + 1],
        "edges": [],
        "focus": [],
        "optional": False,
    }


def _arrow_count(src: str) -> int:
    """Count flowchart / sequence arrows for intra path steps."""
    if not src:
        return 1
    n = len(re.findall(r"(-->>|->>|-->|-.->|->)", src))
    return max(n, 1)


def _code_lines(text: str) -> list[str]:
    return [ln for ln in str(text or "").splitlines()]


def expand_intra_steps(g: dict, letter: str, reveal_groups: list, base_caption: str) -> list[dict]:
    """Walk INSIDE a board's blocks, then the player advances to the next board."""
    out: list[dict] = []
    blocks = [blk for blk in (g.get("blocks") or []) if isinstance(blk, dict)]
    raw_cap = (base_caption or "").strip()
    # strip a leading "A · " / "A: " badge from author captions (not the first letter of a word)
    if letter:
        raw_cap = re.sub(rf"^{re.escape(letter)}\s*[·.:：]\s*", "", raw_cap)
    base = raw_cap or str(g.get("title") or g.get("id") or "")

    def add(kind: str, block: int, i: int, n: int, detail: str):
        out.append({
            "edges": [],
            "focus": [],
            "caption": base,
            "sub": detail,
            "optional": False,
            "group": g["id"],
            "reveal_groups": list(reveal_groups),
            "intra": {"kind": kind, "block": block, "i": i, "n": n},
        })

    if not blocks:
        add("board", 0, 0, 1, "")
        return out

    for bi, blk in enumerate(blocks):
        kind = str(blk.get("type") or "callout")
        if kind == "chips":
            items = blk.get("items") or []
            n = max(len(items), 1)
            for i in range(n):
                lab = str((items[i] or {}).get("label") if isinstance(items[i], dict) else items[i])[:12]
                add("chips", bi, i, n, f"{lab}（{i+1}/{n}）")
        elif kind == "callout":
            add("callout", bi, 0, 1, "")
        elif kind == "text":
            add("text", bi, 0, 1, "")
        elif kind == "list":
            items = blk.get("items") or []
            n = max(len(items), 1)
            for i in range(n):
                add("list", bi, i, n, f"第 {i+1} 条，共 {n} 条")
        elif kind == "table":
            rows = [r for r in (blk.get("rows") or []) if isinstance(r, (list, tuple))]
            n = max(len(rows), 1)
            for i in range(len(rows) or 1):
                head = str(rows[i][0]) if i < len(rows) and rows[i] else ""
                head = re.sub(r"^(ok|x|×|✓|!)\s+", "", head)[:16]
                add("table", bi, i, n, f"{head}（第 {i+1} 行，共 {n} 行）" if head else f"第 {i+1} 行，共 {n} 行")
        elif kind == "bars":
            items = blk.get("items") or []
            n = max(len(items), 1)
            for i, it in enumerate(items):
                lab = str(it.get("label") or "")
                add("bars", bi, i, n, f"{lab}（{i+1}/{n}）" if lab else f"{i+1}/{n}")
        elif kind in {"flowchart", "sequence", "mermaid"}:
            n = _arrow_count(str(blk.get("mermaid") or ""))
            label = "消息" if kind == "sequence" else "连线"
            for i in range(n):
                add(kind, bi, i, n, f"第 {i+1} 条{label}，共 {n} 条")
        elif kind == "code":
            lines = _code_lines(str(blk.get("text") or ""))
            nonempty = [i for i, ln in enumerate(lines) if ln.strip()]
            if not nonempty:
                nonempty = [0]
                lines = lines or [""]
            n = len(nonempty)
            for j, li in enumerate(nonempty):
                add("code", bi, li, n, f"第 {j+1} 行代码，共 {n} 行")
        else:
            add(kind, bi, 0, 1, "")
    return out


def board_play(data: dict) -> dict:
    """Play object: intra-block steps first, then next board (A→J).

    display=lesson uses one step per board; the reader takes one board at a time.
    """
    expand_board_steps(data)
    letters = board_letters(data)
    by_id = {str(g["id"]): g for g in (data.get("groups") or []) if isinstance(g, dict) and g.get("id")}
    lesson = str(data.get("display") or "").lower() == "lesson"
    steps: list[dict] = []
    for st in data.get("steps") or []:
        if not isinstance(st, dict) or "reveal_groups" not in st:
            continue
        gid = str(st.get("group") or "")
        g = by_id.get(gid)
        if not g:
            continue
        letter = letters.get(gid, "")
        cap = str(st.get("caption") or "")
        if lesson:
            # strip leading letter chrome from caption
            raw = cap.strip()
            if letter:
                raw = re.sub(rf"^{re.escape(letter)}\s*[·.:：]\s*", "", raw)
            raw = raw or str(g.get("title") or gid)
            steps.append({
                "edges": [],
                "focus": [],
                "caption": raw,
                "optional": False,
                "group": gid,
                "reveal_groups": list(st.get("reveal_groups") or []),
                "intra": {"kind": "board", "block": 0, "i": 0, "n": 1},
            })
        else:
            steps.extend(expand_intra_steps(g, letter, list(st.get("reveal_groups") or []), cap))
    return {"steps": steps, "edges": [], "start": []}


def render_block(b: dict, board_i: int = 0, block_i: int = 0) -> str:
    kind = b.get("type")
    inner = _render_block_inner(b, board_i, block_i)
    if not inner:
        return ""
    return f'<div class="cv-block" data-kind="{html_escape(str(kind))}" data-block="{block_i}" style="--i:{block_i}">{inner}</div>'


def _render_block_inner(b: dict, board_i: int = 0, block_i: int = 0) -> str:
    kind = b.get("type")
    if kind == "chips":
        parts = []
        for i, it in enumerate(b.get("items") or []):
            lab = html_escape(str(it.get("label") or ""))
            sub = html_escape(str(it.get("sub") or ""))
            sub_html = f'<span class="cv-tok-idx">{sub}</span>' if sub else ""
            parts.append(f'<div class="cv-tok" data-tok="{i}" data-i="{i}">{lab}{sub_html}</div>')
        return f'<div class="cv-tokens" data-kind="chips">{"".join(parts)}</div>'
    if kind == "callout":
        tone = html_escape(str(b.get("tone") or "info"))
        return f'<aside class="cv-callout tone-{tone}"><div class="cv-callout-bar"></div><p>{_nl(b.get("text"))}</p></aside>'
    if kind == "text":
        return f'<p class="cv-board-text">{_nl(b.get("text"))}</p>'
    if kind == "list":
        lis = "".join(
            f'<li class="cv-li" data-li="{i}">{_cell(x)}</li>'
            for i, x in enumerate(b.get("items") or [])
        )
        return f'<ul class="cv-board-list">{lis}</ul>'
    if kind == "table":
        headers = b.get("headers") or []
        rows = [r for r in (b.get("rows") or []) if isinstance(r, (list, tuple))]
        width = max([len(headers)] + [len(r) for r in rows] + [0])
        num_col = [bool(rows) and all(i < len(r) and _is_num(r[i]) for r in rows) for i in range(width)]
        num = ' class="num"'
        thead = "".join(
            f'<th{num if i < width and num_col[i] else ""}>{html_escape(str(h))}</th>'
            for i, h in enumerate(headers)
        )
        rows_html = []
        for ri, row in enumerate(b.get("rows") or []):
            if not isinstance(row, (list, tuple)):
                continue
            cells = "".join(
                f'<td{num if ci < width and num_col[ci] else ""}>{_cell(c)}</td>' for ci, c in enumerate(row)
            )
            rows_html.append(f'<tr data-row="{ri}" class="cv-tr cv-tr-pending">{cells}</tr>')
        return (
            f'<div class="cv-table-wrap"><table class="cv-table">'
            f'<thead><tr>{thead}</tr></thead><tbody>{"".join(rows_html)}</tbody></table></div>'
        )
    if kind == "bars":
        parts = []
        for bi, it in enumerate(b.get("items") or []):
            val = float(it["value"])
            mx = float(it["max"]) or 1.0
            pct = max(0.0, min(100.0, 100.0 * val / mx))
            unit = html_escape(it.get("unit") or "")
            lab = html_escape(it["label"])
            if (it.get("unit") or "") == "%":
                val_txt = f"{_fmt(val)}%"
            else:
                val_txt = f"{_fmt(val)} / {_fmt(mx)}{(' ' + unit) if unit else ''}"
            ticks = "".join(
                f'<span style="left:{100.0 * t / mx:.3f}%">{_tick(t)}</span>' for t in _ticks(mx)
            )
            parts.append(
                f'<div class="cv-bar-row cv-bar-pending" data-bar="{bi}" data-pct="{pct:.2f}">'
                f'<div class="cv-bar-meta"><span class="cv-bar-label">{lab}</span>'
                f'<span class="cv-bar-val">{val_txt}</span></div>'
                f'<div class="cv-bar-track">'
                f'<i class="cv-bar-fill" style="width:0%;--j:{bi}"></i>'
                f'<i class="cv-bar-needle" style="left:0%"></i></div>'
                f'<div class="cv-bar-ticks" aria-hidden="true">{ticks}</div></div>'
            )
        note = b.get("note") or b.get("n")
        foot = f'<p class="cv-bar-note">{html_escape(str(note))}</p>' if note else ""
        return f'<div class="cv-bars">{"".join(parts)}{foot}</div>'
    if kind in {"flowchart", "sequence", "mermaid"}:
        src = str(b.get("mermaid") or "").rstrip()
        if not src:
            return ""
        n = _arrow_count(src)
        return (
            f'<div class="cv-md" data-md-kind="{html_escape(kind)}" data-md-board="{board_i}" '
            f'data-md-block="{block_i}" data-md-steps="{n}">'
            f'<pre class="cv-md-src">{html_escape(src)}</pre></div>'
        )
    if kind == "code":
        lang = html_escape(str(b.get("lang") or ""))
        lines = _code_lines(str(b.get("text") or ""))
        body = "".join(
            f'<span class="cv-code-line cv-code-pending" data-line="{i}">{html_escape(ln) if ln else "&nbsp;"}</span>'
            for i, ln in enumerate(lines)
        )
        return f'<pre class="cv-code" data-lang="{lang}"><code>{body}</code></pre>'
    return ""


def _cell(c: Any) -> str:
    s = str(c)
    # light semantic markers: ! warn, x bad, ok/check
    if s.startswith("! "):
        return f'<span class="cv-sym warn">!</span>{html_escape(s[2:])}'
    if (s.startswith("x ") or s.startswith("× ")) and not re.match(r"^[x×]\s+[\d×*/÷+\-=·]", s):
        return f'<span class="cv-sym bad">×</span>{html_escape(s[2:])}'
    if s.startswith("ok ") or s.startswith("✓ "):
        return f'<span class="cv-sym ok">✓</span>{html_escape(s[3:] if s.startswith("ok ") else s[2:])}'
    # monospace-ish tokens
    if "`" in s:
        parts = []
        odd = False
        for chunk in s.split("`"):
            if odd:
                parts.append(f"<code>{html_escape(chunk)}</code>")
            else:
                parts.append(html_escape(chunk))
            odd = not odd
        return "".join(parts)
    return html_escape(s)


_NUM_RE = re.compile(r"^[\s+\-−~≈]*\d[\d,]*(\.\d+)?\s*(%|[A-Za-z]{0,3})\s*$")


def _is_num(c: Any) -> bool:
    return bool(_NUM_RE.match(str(c)))


def _nl(text: Any) -> str:
    return html_escape(str(text or "")).replace("\n", "<br>")


def _ticks(mx: float) -> list[float]:
    """Round tick values for a gauge scale 0..mx: steps of 1, 2, 2.5 or 5 times a power of ten."""
    if mx <= 0:
        return [0.0]
    raw = mx / 5
    mag = 10 ** math.floor(math.log10(raw))
    step = next(k * mag for k in (1, 2, 2.5, 5, 10) if k * mag >= raw)
    out, t = [], 0.0
    while t <= mx + 1e-9:
        out.append(round(t, 6))
        t += step
    if mx - out[-1] > 0.12 * mx:  # a long unlabelled tail: name the limit itself
        out.append(mx)
    return out


def _tick(t: float) -> str:
    if t >= 1_000_000:
        return f"{t / 1_000_000:.3g}M"
    if t >= 1000:
        return f"{t / 1000:.3g}k"
    return _fmt(t)


def _fmt(n: float) -> str:
    if abs(n - int(n)) < 1e-9:
        return str(int(n))
    return f"{n:.4g}"


def board_letters(data: dict) -> dict[str, str]:
    """A, B, C… for groups that carry blocks (dense teaching boards)."""
    out: dict[str, str] = {}
    i = 0
    for g in data.get("groups") or []:
        if not isinstance(g, dict) or not g.get("id"):
            continue
        if not (g.get("blocks") or g.get("b")):
            continue
        o = g.get("badge")
        out[str(g["id"])] = str(o)[:3] if o else _letter_for(i)
        i += 1
    return out


def render_boards(data: dict, letters: dict[str, str] | None = None, cols: int | None = None) -> str:
    letters = letters or board_letters(data)
    ori = str(data.get("orientation") or "landscape").lower()
    if ori not in {"landscape", "portrait"}:
        ori = "landscape"
    if cols is None:
        raw = data.get("cols") or data.get("columns")
        if isinstance(raw, int) and 1 <= raw <= 6:
            cols = raw
        else:
            n = sum(1 for g in (data.get("groups") or []) if isinstance(g, dict) and (g.get("blocks") or g.get("b") or []))
            cols = 1 if n <= 1 else (2 if n <= 4 else 3)
    parts = [
        f'<div class="cv-sheet" data-sheet="1">'
        f'<div class="cv-boards" data-boards="1" data-orientation="{ori}" data-cols="{int(cols)}" '
        f'style="--cv-cols:{int(cols)}">'
    ]
    idx = 0
    for g in data.get("groups") or []:
        if not isinstance(g, dict) or not g.get("id"):
            continue
        if not (g.get("blocks") or []):
            continue
        gid = str(g["id"])
        letter = html_escape(str(letters.get(gid) or g.get("letter") or ""))
        title = html_escape(str(g.get("title") or gid))
        sub = g.get("subtitle") or g.get("sub")
        sub_html = f'<span class="cv-board-sub">{html_escape(str(sub))}</span>' if sub else ""
        span = g.get("span") or g.get("colspan") or g.get("sp") or 1
        try:
            span = int(span)
        except (TypeError, ValueError):
            span = 1
        span = max(1, min(int(cols), span))
        span_attr = f' data-span="{span}"' if span > 1 else ""
        span_style = f' style="grid-column: span {span};"' if span > 1 else ""
        body_parts = []
        for bi, blk in enumerate(g["blocks"]):
            if not isinstance(blk, dict):
                continue
            body_parts.append(render_block(blk, board_i=idx, block_i=bi))
        parts.append(
            f'<section class="cv-board cv-group" data-group-id="{html_escape(gid)}" '
            f'data-letter="{letter}" data-board-i="{idx}" data-members=""{span_attr}{span_style}>'
            f'<div class="cv-board-head"><span class="cv-group-badge cv-board-badge">{letter}</span>'
            f'<span class="cv-board-title">{title}</span>{sub_html}'
            f'<button type="button" class="cv-board-zoom" title="单独放大这一块" aria-label="放大">⤢</button></div>'
            f'<div class="cv-board-body">{"".join(body_parts)}</div></section>'
        )
        idx += 1
    parts.append("</div></div>")
    return "".join(parts)


def _normalize_tokens(data: dict) -> list[dict]:
    raw = data.get("tokens") if data.get("tokens") is not None else data.get("tok")
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for i, it in enumerate(raw):
        if isinstance(it, str):
            out.append({"label": it, "sub": ""})
        elif isinstance(it, dict):
            lab = str(it.get("label") or it.get("l") or "")
            sub = str(it.get("sub") or it.get("s") or it.get("idx") or "")
            out.append({"label": lab, "sub": sub})
    return out


def render_lesson(data: dict, letters: dict[str, str] | None = None) -> str:
    """Single-stage stepped teaching page: one board visible, chips above, outline at step 0."""
    letters = letters or board_letters(data)
    tokens = _normalize_tokens(data)
    tok_parts = []
    for i, t in enumerate(tokens):
        lab = html_escape(t["label"])
        sub = html_escape(t["sub"])
        sub_html = f'<span class="cv-tok-idx">{sub}</span>' if sub else ""
        tok_parts.append(f'<div class="cv-tok" data-tok="{i}" data-i="{i}">{lab}{sub_html}</div>')
    tok_html = (
        f'<div class="cv-lesson-tokens cv-tokens" id="cv-lesson-tokens">{"".join(tok_parts)}</div>'
        if tok_parts else ""
    )
    parts = [
        '<div class="cv-lesson" data-lesson="1">',
        '<div class="cv-lesson-stage">',
        tok_html,
        '<div class="cv-boards cv-lesson-boards" data-boards="1" data-orientation="landscape" '
        'data-cols="1" style="--cv-cols:1">',
    ]
    idx = 0
    for g in data.get("groups") or []:
        if not isinstance(g, dict) or not g.get("id"):
            continue
        if not (g.get("blocks") or []):
            continue
        gid = str(g["id"])
        letter = html_escape(str(letters.get(gid) or g.get("letter") or ""))
        title = html_escape(str(g.get("title") or gid))
        focus = g.get("focus_tokens")
        if focus is None:
            focus = g.get("ft")
        focus_attr = ""
        if isinstance(focus, list):
            focus_attr = f' data-focus-tokens="{",".join(str(int(x)) for x in focus)}"'
        body_parts = []
        for bi, blk in enumerate(g["blocks"]):
            if isinstance(blk, dict):
                body_parts.append(render_block(blk, board_i=idx, block_i=bi))
        parts.append(
            f'<section class="cv-board cv-group cv-lesson-panel" data-group-id="{html_escape(gid)}" '
            f'data-letter="{letter}" data-board-i="{idx}" data-members=""{focus_attr}>'
            f'<div class="cv-board-head">'
            f'<span class="cv-group-badge cv-board-badge" hidden>{letter}</span>'
            f'<span class="cv-board-title">{title}</span></div>'
            f'<div class="cv-board-body">{"".join(body_parts)}</div></section>'
        )
        idx += 1
    parts.append("</div></div></div>")
    return "".join(parts)
