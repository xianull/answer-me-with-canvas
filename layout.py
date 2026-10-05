"""Deterministic layout for canvas-content/v0 (content has no coordinates).

Used by the built-in group/lane renderer and by every export that needs geometry
(Obsidian .canvas, Excalidraw).

Model
  * ``groups[]`` = pedagogical section boards (answer-me-with-html panel style):
    stacked top→bottom as successive teaching units. Nodes flow left→right inside
    each board by local rank.
  * ``lanes[]`` + ``node.lane`` = swimlanes: parallel columns for sequence diagrams
    (Client/Server). Shared ranks so messages slope downward.
  * Without any region the old rank layout is used (rows or columns by direction).
"""

from __future__ import annotations

FONT_W_ASCII = 9.0
FONT_W_CJK = 16.0
LINE_H = 23
PAD_X = 18
PAD_Y = 14
MIN_W = 140
GAP_COL = 48          # between regions
GAP_COL_LANES = 80    # lanes carry message labels between them
GAP_ROW = 28
GAP_SIDE = 22         # nodes side by side inside one region
HEAD = 58             # region title + subtitle band (letter + title + sub)
GPAD = 20             # padding inside a region
MARGIN = 24
SECTION_GAP = 40      # vertical gap between pedagogical section boards
BOARD_MIN_W = 720  # lesson-board minimum width
BOARD_MIN_W_GRID = 360  # a section board when sections tile in a grid
GAP_COL_GRID = 56       # between tiled section boards; cross-board edges run through it


def label_lines(label) -> list:
    return str(label or "").replace("\r\n", "\n").split("\n")


def text_px(line: str, size_scale: float = 1.0) -> float:
    return sum(FONT_W_CJK if ord(c) > 0x2E7F else FONT_W_ASCII for c in line) * size_scale


def node_size(label) -> tuple:
    lines = label_lines(label)
    first = text_px(lines[0], 1.08)  # title line is bold and a bit larger
    rest = max((text_px(l, 0.92) for l in lines[1:]), default=0)
    width = max(MIN_W, max(first, rest) + 2 * PAD_X)
    height = len(lines) * LINE_H + 2 * PAD_Y
    return round(width), round(height)


def _edges(data: dict) -> list:
    return [e for e in data.get("edges") or [] if isinstance(e, dict) and e.get("from") and e.get("to")]


def ranks(node_ids: list, edges: list) -> dict:
    succ = {n: [] for n in node_ids}
    for e in edges:
        if e["from"] in succ and e["to"] in succ:
            succ[e["from"]].append(e["to"])
    state = {n: 0 for n in node_ids}
    forward = {n: [] for n in node_ids}
    for root in node_ids:
        if state[root]:
            continue
        stack = [(root, iter(succ[root]))]
        state[root] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                continue
            if state[nxt] == 1:
                continue  # back edge
            forward[node].append(nxt)
            if state[nxt] == 0:
                state[nxt] = 1
                stack.append((nxt, iter(succ[nxt])))
    indeg = {n: 0 for n in node_ids}
    for n in node_ids:
        for m in forward[n]:
            indeg[m] += 1
    rank = {n: 0 for n in node_ids}
    queue = [n for n in node_ids if indeg[n] == 0]
    while queue:
        n = queue.pop(0)
        for m in forward[n]:
            rank[m] = max(rank[m], rank[n] + 1)
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    return rank


def _letter(i: int, decl: list, gid: str, is_group: bool) -> str:
    """A, B, C ... by display order; `label` (groups) or `badge` (groups or lanes) overrides."""
    for d in decl:
        if isinstance(d, dict) and str(d.get("id")) == gid:
            o = d.get("badge") or (d.get("label") if is_group else None)
            if o:
                return str(o)[:3]
    return letter_for(i)


def letter_for(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def page_letters(data: dict) -> dict:
    """Letters for page regions (groups whose members start with @), continuing after diagram groups."""
    used = len([g for g in resolve_groups(data) if g["id"] != "_loose"])
    out = {}
    for g in data.get("groups") or []:
        if isinstance(g, dict) and g.get("id") and any(str(m).startswith("@") for m in g.get("members") or []):
            o = g.get("badge") or g.get("label")
            out[str(g["id"])] = str(o)[:3] if o else letter_for(used)
            used += 1
    return out


def resolve_groups(data: dict) -> list:
    """Regions as [{id, title, subtitle, role, members, full}] in display order."""
    node_ids = [str(n["id"]) for n in data.get("nodes") or [] if isinstance(n, dict) and n.get("id")]
    known = set(node_ids)
    out = []
    taken = set()
    if data.get("groups"):
        for g in data["groups"]:
            if not isinstance(g, dict) or not g.get("id"):
                continue
            members = [str(m) for m in g.get("members") or [] if str(m) in known and str(m) not in taken]
            members += [str(n["id"]) for n in data.get("nodes") or []
                        if isinstance(n, dict) and str(n.get("group") or "") == str(g["id"])
                        and str(n["id"]) not in members and str(n["id"]) not in taken]
            has_blocks = bool(g.get("blocks") or g.get("b"))
            if not members and not has_blocks:
                continue
            taken.update(members)
            out.append({"id": str(g["id"]), "title": str(g.get("title") or g["id"]),
                        "subtitle": str(g.get("subtitle") or ""), "role": str(g.get("role") or ""),
                        "members": members, "full": False, "under": str(g.get("under") or ""),
                        "blocks": list(g.get("blocks") or [])})
    else:
        lanes = [l for l in data.get("lanes") or [] if isinstance(l, dict) and l.get("id")]
        order = [str(l["id"]) for l in lanes]
        meta = {str(l["id"]): l for l in lanes}
        for n in data.get("nodes") or []:
            lane = str(n.get("lane") or "") if isinstance(n, dict) else ""
            if lane and lane not in order:
                order.append(lane)
        for lane in order:
            members = [str(n["id"]) for n in data.get("nodes") or [] if isinstance(n, dict) and str(n.get("lane") or "") == lane]
            if not members:
                continue
            taken.update(members)
            m = meta.get(lane, {})
            out.append({"id": lane, "title": str(m.get("label") or m.get("title") or lane),
                        "subtitle": str(m.get("subtitle") or ""), "role": str(m.get("role") or ""),
                        "members": members, "full": True})
    for i, g in enumerate(out):
        g["letter"] = _letter(i, (data.get("groups") if data.get("groups") else data.get("lanes")) or [], g["id"], bool(data.get("groups")))
    if out:
        loose = [n for n in node_ids if n not in taken]
        if loose:
            out.append({"id": "_loose", "title": "", "subtitle": "", "role": "", "members": loose,
                        "full": out[0]["full"]})
    return out


def layout(data: dict) -> dict:
    nodes = [n for n in data.get("nodes") or [] if isinstance(n, dict) and n.get("id")]
    ids = [str(n["id"]) for n in nodes]
    edges = _edges(data)
    rank = ranks(ids, edges)
    size = {str(n["id"]): node_size(n.get("label") or n["id"]) for n in nodes}
    groups = resolve_groups(data)
    if not groups:
        return _plain_layout(data, ids, size, rank)

    lanes_mode = groups[0]["full"]
    boxes = {}
    regions = []
    gap = (GAP_COL_LANES if len(groups) <= 2 else GAP_COL) if lanes_mode else GAP_COL

    if lanes_mode:
        # Swimlanes: shared ranks so messages slope like a sequence diagram.
        used = sorted({rank[i] for i in ids})
        dense = {r: k for k, r in enumerate(used)}
        row_of = {i: dense[rank[i]] for i in ids}
        nrows = len(used)
        row_h = [0] * nrows
        for i in ids:
            row_h[row_of[i]] = max(row_h[row_of[i]], size[i][1])
        row_y = []
        y = MARGIN + HEAD + GPAD // 2
        for r in range(nrows):
            row_y.append(y)
            y += row_h[r] + GAP_ROW
        bottom = y - GAP_ROW + GPAD
        columns = [{"groups": [g], "rows": {row_of[m] for m in g["members"]}} for g in groups]
        x = MARGIN
        for col in columns:
            g = col["groups"][0]
            col_w = max(size[m][0] for m in g["members"])
            by_row = {}
            for m in g["members"]:
                by_row.setdefault(row_of[m], []).append(m)
            inner = max(len(v) * col_w + (len(v) - 1) * GAP_SIDE for v in by_row.values())
            title_w = max(text_px(g["title"] + "  " + (g.get("letter") or ""), 1.0),
                          text_px(g["subtitle"], 0.85)) + 2 * GPAD + 36
            width = max(inner + 2 * GPAD, title_w)
            for r, members in by_row.items():
                span = len(members) * col_w + (len(members) - 1) * GAP_SIDE
                cx = x + (width - span) / 2
                for m in members:
                    h = size[m][1]
                    boxes[m] = (round(cx), round(row_y[r] + (row_h[r] - h) / 2), col_w, h)
                    cx += col_w + GAP_SIDE
            regions.append({**g, "x": round(x), "y": MARGIN, "w": round(width), "h": round(bottom - MARGIN)})
            x += width + gap
        width = x - gap + MARGIN
        height = bottom + MARGIN
        return {"nodes": boxes, "groups": regions, "lanes": regions, "width": round(width), "height": round(height),
                "rank": rank, "lanes_mode": True}

    # Pedagogical sections (groups): stacked full-width lesson boards
    # (answer-me-with-html panel style). Nodes flow left→right by local rank.
    # `lanes` stay as parallel swimlanes above; only `groups` use this path.
    prepared = []
    for g in groups:
        local = ranks(g["members"], [e for e in edges if e["from"] in g["members"] and e["to"] in g["members"]])
        used = sorted({local[m] for m in g["members"]})
        dense = {r: k for k, r in enumerate(used)}
        local_col = {m: dense[local[m]] for m in g["members"]}
        by_col = {}
        order = {m: i for i, m in enumerate(g["members"])}
        for m in g["members"]:
            by_col.setdefault(local_col[m], []).append(m)
        for c in by_col:
            by_col[c].sort(key=lambda m: order[m])
        ncols = len(used)
        col_w = [max(size[m][0] for m in by_col[c]) for c in range(ncols)]
        col_h = [sum(size[m][1] for m in by_col[c]) + GAP_ROW * (len(by_col[c]) - 1) for c in range(ncols)]
        inner_w = sum(col_w) + GAP_SIDE * (ncols - 1) if ncols else 0
        inner_h = max(col_h) if col_h else 0
        title_w = max(text_px(g["title"] + "  " + (g.get("letter") or ""), 1.05),
                      text_px(g["subtitle"], 0.9)) + 2 * GPAD + 48
        prepared.append((g, by_col, col_w, col_h, inner_w, inner_h, title_w))

    grid_cols = _section_cols(data, len(prepared))
    if grid_cols > 1:
        return _section_grid(prepared, grid_cols, size, boxes, regions, rank)
    board_w = max(BOARD_MIN_W, max(p[4] + 2 * GPAD for p in prepared), max(p[6] for p in prepared))
    y = MARGIN
    for g, by_col, col_w, col_h, inner_w, inner_h, title_w in prepared:
        top = y
        body_top = y + HEAD + GPAD // 2
        # center the node flow inside the board
        x0 = MARGIN + (board_w - inner_w) / 2
        cx = x0
        for c, members in sorted(by_col.items()):
            stack_h = col_h[c]
            cy = body_top + (inner_h - stack_h) / 2
            for m in members:
                w, h = size[m][0], size[m][1]
                # use column width so nodes in a column align
                boxes[m] = (round(cx + (col_w[c] - w) / 2), round(cy), w, h)
                cy += h + GAP_ROW
            cx += col_w[c] + GAP_SIDE
        bot = body_top + inner_h + GPAD
        regions.append({**g, "x": MARGIN, "y": round(top), "w": round(board_w), "h": round(bot - top)})
        y = bot + SECTION_GAP
    width = board_w + 2 * MARGIN
    height = y - SECTION_GAP + MARGIN
    return {"nodes": boxes, "groups": regions, "lanes": regions, "width": round(width), "height": round(height),
            "rank": rank, "lanes_mode": False, "sections_mode": True}


def _section_cols(data: dict, n: int) -> int:
    """Landscape pages tile their sections in a grid (2 across, 3 when there are 5 or more);
    portrait pages, or pages with fewer than 3 sections, stack them."""
    if str(data.get("orientation") or "landscape").lower() == "portrait" or n < 2:
        return 1
    raw = data.get("section_cols")
    if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= 4:
        return min(raw, n)
    if n < 3:
        return 1
    return 3 if n >= 5 else 2


def _section_grid(prepared: list, ncols: int, size: dict, boxes: dict, regions: list, rank: dict) -> dict:
    """Sections in reading order, row by row. A column is as wide as its widest board,
    a row as tall as its tallest; each board centers its own node flow."""
    rows = [prepared[i:i + ncols] for i in range(0, len(prepared), ncols)]
    col_w = [BOARD_MIN_W_GRID] * ncols
    for row in rows:
        for ci, p in enumerate(row):
            col_w[ci] = max(col_w[ci], p[4] + 2 * GPAD, p[6])
    y = MARGIN
    for row in rows:
        row_inner = max(p[5] for p in row)
        row_h = HEAD + GPAD // 2 + row_inner + GPAD
        x = MARGIN
        for ci, (g, by_col, cw, ch, inner_w, inner_h, title_w) in enumerate(row):
            bw = col_w[ci]
            body_top = y + HEAD + GPAD // 2
            cx = x + (bw - inner_w) / 2
            for c, members in sorted(by_col.items()):
                cy = body_top + (row_inner - ch[c]) / 2
                for m in members:
                    w, h = size[m][0], size[m][1]
                    boxes[m] = (round(cx + (cw[c] - w) / 2), round(cy), w, h)
                    cy += h + GAP_ROW
                cx += cw[c] + GAP_SIDE
            regions.append({**g, "x": round(x), "y": round(y), "w": round(bw), "h": round(row_h)})
            x += bw + GAP_COL_GRID
        y += row_h + SECTION_GAP
    width = sum(col_w) + GAP_COL_GRID * (ncols - 1) + 2 * MARGIN
    height = y - SECTION_GAP + MARGIN
    return {"nodes": boxes, "groups": regions, "lanes": regions, "width": round(width), "height": round(height),
            "rank": rank, "lanes_mode": False, "sections_mode": True, "grid": ncols}


def _plain_layout(data, ids, size, rank) -> dict:
    horizontal = str(data.get("direction") or "TB").upper() in {"LR", "RL"}
    by_rank = {}
    for nid in ids:
        by_rank.setdefault(rank[nid], []).append(nid)
    boxes = {}
    pos = MARGIN
    extent = 0
    lines_of = []
    for r in sorted(by_rank):
        members = by_rank[r]
        thick = max((size[m][0] if horizontal else size[m][1]) for m in members)
        lines_of.append((pos, thick, members))
        pos += thick + (GAP_COL if horizontal else GAP_ROW + 20)
    for start, thick, members in lines_of:
        cross = MARGIN
        for m in members:
            w, h = size[m]
            if horizontal:
                boxes[m] = (round(start + (thick - w) / 2), round(cross), w, h)
                cross += h + GAP_ROW
            else:
                boxes[m] = (round(cross), round(start + (thick - h) / 2), w, h)
                cross += w + 60
        extent = max(extent, cross)
    if horizontal:
        width, height = pos - GAP_COL + MARGIN, extent + MARGIN
    else:
        width, height = extent + MARGIN, pos + MARGIN
        for start, thick, members in lines_of:
            row_w = sum(size[m][0] for m in members) + 60 * (len(members) - 1)
            shift = (width - 2 * MARGIN - row_w) / 2
            for m in members:
                bx, by, w, h = boxes[m]
                boxes[m] = (round(bx + shift), by, w, h)
    return {"nodes": boxes, "groups": [], "lanes": [], "width": round(width), "height": round(height),
            "rank": rank, "lanes_mode": False}


def anchor(box, toward) -> tuple:
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    tx, ty = toward
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    sx = (w / 2) / abs(dx) if dx else float("inf")
    sy = (h / 2) / abs(dy) if dy else float("inf")
    s = min(sx, sy)
    return cx + dx * s, cy + dy * s


def edge_line(boxes: dict, left: str, right: str) -> tuple:
    a, b = boxes[left], boxes[right]
    ca = (a[0] + a[2] / 2, a[1] + a[3] / 2)
    cb = (b[0] + b[2] / 2, b[1] + b[3] / 2)
    return anchor(a, cb) + anchor(b, ca)


def _group_of(L: dict, node: str):
    for g in L.get("groups") or []:
        if node in g["members"]:
            return g
    return None


def route(L: dict, left: str, right: str) -> list:
    """Polyline points for an edge; the middle run steps aside if an earlier edge already uses that line."""
    pts = _route(L, left, right)
    if L.get("lanes_mode") or len(pts) < 4:
        return pts
    claims = L.setdefault("_claims", [])
    pts = [list(p) for p in pts]
    for i in range(1, len(pts) - 2):
        p, q = pts[i], pts[i + 1]
        axis = 1 if abs(p[1] - q[1]) < 0.5 else (0 if abs(p[0] - q[0]) < 0.5 else None)
        if axis is None:
            continue
        lo, hi = sorted((p[1 - axis], q[1 - axis]))
        base = p[axis]
        for shift in (0, 8, -8, 16, -16, 24, -24):
            c = base + shift
            if not any(ax == axis and abs(cc - c) < 4 and min(hi, h2) - max(lo, l2) > 4 for ax, cc, l2, h2 in claims):
                break
        p[axis] = q[axis] = c
        claims.append((axis, c, lo, hi))
    return [tuple(p) for p in pts]


def _route(L: dict, left: str, right: str) -> list:
    """Polyline points for an edge.

    * same region, target below: vertical (with one horizontal jog if not aligned)
    * target above (loop back): leave right, run up outside the region, come back in
    * otherwise: straight between the facing borders
    """
    boxes = L["nodes"]
    a, b = boxes[left], boxes[right]
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ga, gb = _group_of(L, left), _group_of(L, right)
    same = ga is not None and ga is gb
    if by >= ay + ah - 1 and (same or not ga):
        x1, y1 = ax + aw / 2, ay + ah
        x2, y2 = bx + bw / 2, by
        if abs(x1 - x2) < 2:
            return [(x1, y1), (x2, y2)]
        mid = (y1 + y2) / 2
        return [(x1, y1), (x1, mid), (x2, mid), (x2, y2)]
    if by + bh <= ay + 1 and (same or not ga):
        right_edge = max(ax + aw, bx + bw)
        if ga is not None:
            right_edge = ga["x"] + ga["w"] - 6
        gx = right_edge
        return [(ax + aw, ay + ah / 2), (gx, ay + ah / 2), (gx, by + bh / 2), (bx + bw, by + bh / 2)]
    if not L.get("lanes_mode") and ga is not None and gb is not None and ga is not gb:
        # pedagogical sections stack vertically: route through the gap between boards
        if gb["y"] >= ga["y"] + ga["h"] - 2:
            x1, y1 = ax + aw / 2, ay + ah
            x2, y2 = bx + bw / 2, by
            gap_y = (ga["y"] + ga["h"] + gb["y"]) / 2
            if abs(x1 - x2) < 2:
                return [(x1, y1), (x2, y2)]
            return [(x1, y1), (x1, gap_y), (x2, gap_y), (x2, y2)]
        if ga["y"] >= gb["y"] + gb["h"] - 2:
            x1, y1 = ax + aw / 2, ay
            x2, y2 = bx + bw / 2, by + bh
            gap_y = (gb["y"] + gb["h"] + ga["y"]) / 2
            return [(x1, y1), (x1, gap_y), (x2, gap_y), (x2, y2)]
        # side by side (sections tiled in a grid): straight across the column gap, unless that
        # would run through other boxes; then loop under or over the row instead
        y1, y2 = ay + ah / 2, by + bh / 2
        if gb["x"] >= ga["x"] + ga["w"]:
            gx = (ga["x"] + ga["w"] + gb["x"]) / 2
            straight = [(ax + aw, y1), (gx, y1), (gx, y2), (bx, y2)]
        else:
            gx = (gb["x"] + gb["w"] + ga["x"]) / 2
            straight = [(ax, y1), (gx, y1), (gx, y2), (bx + bw, y2)]
        top = min(ga["y"], gb["y"])
        bottom = max(ga["y"] + ga["h"], gb["y"] + gb["h"])
        above_y = top - (SECTION_GAP / 2 if top > MARGIN + 1 else MARGIN / 2)
        below_y = bottom + (SECTION_GAP / 2 if bottom + MARGIN < L["height"] - 1 else MARGIN / 2)
        x1, x2 = ax + aw / 2, bx + bw / 2
        under = [(x1, ay + ah), (x1, below_y), (x2, below_y), (x2, by + bh)]
        over = [(x1, ay), (x1, above_y), (x2, above_y), (x2, by)]
        backward = gb["x"] < ga["x"]
        options = [straight] + ([under, over] if backward else [over, under])
        return min(options, key=lambda pts: _crossings(L, pts, (left, right)))
    x1, y1, x2, y2 = edge_line(boxes, left, right)
    straight = [(x1, y1), (x2, y2)]
    if same and not L.get("lanes_mode") and _crossings(L, straight, (left, right)):
        # two boxes in one section with others between them: hop over (or under) the row
        top, bottom = min(ay, by), max(ay + ah, by + bh)
        cx1, cx2 = ax + aw / 2, bx + bw / 2
        over = [(cx1, ay), (cx1, top - 16), (cx2, top - 16), (cx2, by)]
        under = [(cx1, ay + ah), (cx1, bottom + 16), (cx2, bottom + 16), (cx2, by + bh)]
        return min((straight, over, under), key=lambda pts: _crossings(L, pts, (left, right)))
    return straight


def _crossings(L: dict, pts: list, skip: tuple) -> int:
    """How many boxes (other than the edge's own ends) an axis-aligned polyline runs through."""
    hits = 0
    for nid, (x, y, w, h) in L["nodes"].items():
        if nid in skip:
            continue
        for (px, py), (qx, qy) in zip(pts, pts[1:]):
            lo_x, hi_x, lo_y, hi_y = min(px, qx), max(px, qx), min(py, qy), max(py, qy)
            if hi_x > x + 1 and lo_x < x + w - 1 and hi_y > y + 1 and lo_y < y + h - 1:
                hits += 1
                break
    return hits


def label_point(points: list, boxes=None, size=(0.0, 0.0)) -> tuple:
    """Where an edge label sits: the middle of the longest segment, unless that
    would cover a node; then the nearest spot along the route that stays clear."""
    segs = sorted(zip(points, points[1:]), key=lambda pq: -(abs(pq[1][0] - pq[0][0]) + abs(pq[1][1] - pq[0][1])))
    if not segs:
        return points[0]
    if not boxes:
        p, q = segs[0]
        return ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
    w, h = size
    pad = 4

    def clear(x, y):
        for bx, by, bw, bh in boxes:
            if x + w / 2 + pad > bx and x - w / 2 - pad < bx + bw and y + h / 2 + pad > by and y - h / 2 - pad < by + bh:
                return False
        return True

    for p, q in segs:
        for t in (0.5, 0.35, 0.65, 0.2, 0.8):
            x, y = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
            if clear(x, y):
                return (x, y)
    # no room on the line itself (e.g. two boxes side by side): step off it sideways
    for p, q in segs:
        mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
        horizontal = abs(q[0] - p[0]) >= abs(q[1] - p[1])
        for d in (24, 36, 48, 60):
            for sgn in (-1, 1):
                x, y = (mx, my + sgn * d) if horizontal else (mx + sgn * (d + w / 2 - 12), my)
                if clear(x, y):
                    return (x, y)
    p, q = segs[0]
    return ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)


def side(box, point) -> str:
    x, y, w, h = box
    px, py = point
    d = {"left": abs(px - x), "right": abs(px - x - w), "top": abs(py - y), "bottom": abs(py - y - h)}
    return min(d, key=d.get)
