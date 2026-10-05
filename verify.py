#!/usr/bin/env python3
"""canvas self-check. Prints PASS or FAIL with reasons.

Covers: read-only server (no writeback), content-only schema rules, step resolution,
exports (Obsidian .canvas, Excalidraw), and a headless-browser run of the step player:
page opens in the file's theme, 下一步 advances the step index, clicking the hinted
edge advances, 上一步 goes back, 重来 resets to 0, themes switch.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import export  # noqa: E402
import layout  # noqa: E402
import server  # noqa: E402
import boards  # noqa: E402

reasons: list[str] = []
counted = [0]


def check(ok, why):
    counted[0] += 1
    if not ok:
        reasons.append(why)


def digest(paths):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def static_checks():
    for blocked in ("/etc/passwd", "../prd/x.md"):
        try:
            server.resolve_src(blocked)
            reasons.append(f"path jail allowed {blocked}")
        except ValueError:
            pass
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    check("/writeback" not in src, "server.py still mentions /writeback")
    check("messages.log" not in src, "server.py still mentions messages.log")
    check(not hasattr(server.Handler, "do_POST"), "Handler still has do_POST")
    check("写回" not in server.PAGE, "page still has a 写回 control")
    check(server.CONTENT_FORMAT == "canvas-content/v0", "format is not canvas-content/v0")

    # legacy format accepted once, normalised
    with tempfile.TemporaryDirectory() as tmp:
        old = Path(tmp) / "old.json"
        old.write_text(json.dumps({"format": "live-draft-content/v0", "nodes": [{"id": "a", "label": "A"}], "edges": []}))
        check(server.load_content_json(old)["format"] == "canvas-content/v0", "legacy format not normalised")
        bad = Path(tmp) / "bad.json"
        bad.write_text(json.dumps({"format": "canvas-content/v0", "nodes": [{"id": "a", "label": "A", "x": 1}], "edges": []}))
        try:
            server.load_content_json(bad)
            reasons.append("accepted a node with x")
        except ValueError:
            pass

    # steps: explicit order wins, default = visible edges in order
    edges = [("a", "b"), ("b", "c"), ("a", "c")]
    play = server.build_steps(edges, ["", "", ""], [True, True, False])
    check([s["edges"] for s in play["steps"]] == [["L_a_b_0"], ["L_b_c_0"]], "default step order wrong")
    play = server.build_steps(edges, ["x", "y", "z"], [True, True, True], ["b->c", 0, {"edge": 2, "focus": "a"}])
    check([s["edges"][0] for s in play["steps"]] == ["L_b_c_0", "L_a_b_0", "L_a_c_0"], "explicit steps ignored")
    check(play["steps"][2]["focus"] == ["a"], "step focus ignored")
    try:
        server.build_steps(edges, ["", "", ""], [True] * 3, ["c->a"])
        reasons.append("unknown step edge accepted")
    except ValueError:
        pass

    tcp = server.load_content_json(ROOT / "tcp-handshake.content.json")
    check(len(tcp["steps"]) == 4, "tcp sample should have 4 steps")
    check(all(k not in n for n in tcp["nodes"] for k in ("x", "y", "width", "height")), "tcp sample has coordinates")

    # agent-message sample: pedagogical groups (ask→clarify→deepen→synthesize), detail on every step
    am = server.load_content_json(ROOT / "agent-message.content.json")
    check(len(am.get("groups") or []) >= 4, "agent-message needs >= 4 pedagogical groups")
    check([g["id"] for g in am["groups"][:4]] == ["ask", "clarify", "deepen", "synthesize"],
          "agent-message groups should be ask→clarify→deepen→synthesize")
    check(len(am["steps"]) >= 6 and all(st.get("detail") for st in am["steps"]), "agent-message steps need detail bodies")
    group_of = {}
    for g in am["groups"]:
        for m in g.get("members") or []:
            group_of[m] = g["id"]
    visible = [e for e in am["edges"] if str(e.get("style") or "").lower() != "invisible"]
    cross = [e for e in visible if group_of.get(e["from"]) != group_of.get(e["to"])]
    check(len(cross) >= 3, "agent-message should have edges that move between pedagogical groups")

    # chart block: values + labels only, validated, reveal/highlight resolved
    cd = server.load_content_json(ROOT / "chart-demo.content.json")
    check(cd["chart"]["sample"] is True, "chart-demo must be marked sample data")
    check(cd["chart"]["type"] in ("bar", "line"), "chart type")
    page = server.render_page(ROOT / "chart-demo.content.json")
    check("示例数据" in page and "cv-bar" in page, "chart page lacks bars or sample badge")
    import chart as charts
    for bad in (
        {"type": "pie", "categories": ["a"], "series": [{"values": [1]}]},
        {"type": "bar", "categories": ["a", "b"], "series": [{"values": [1]}]},
        {"type": "bar", "categories": ["a"], "series": [{"values": ["x"]}]},
        {"type": "bar", "categories": ["a"], "series": [{"values": [1]}], "ticks": [0, 1]},
    ):
        try:
            charts.validate_chart(bad)
            reasons.append(f"chart accepted bad input {bad}")
        except ValueError:
            pass
    line = charts.validate_chart({"type": "line", "categories": ["a", "b", "c"], "series": [{"name": "s", "values": [1, None, 3]}]})
    svg = charts.chart_svg(line)
    check(svg.count("cv-dot") == 2 and "cv-seg" not in svg, "line chart: null gap not respected")
    line2 = charts.chart_svg(charts.validate_chart({"type": "line", "categories": ["a", "b"], "series": [{"values": [1, 2]}]}))
    check(line2.count('class="cv-seg') == 1, "line chart lacks a segment")
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "c.json"
        bad.write_text(json.dumps({"format": "canvas-content/v0", "chart": {"type": "bar", "categories": ["a"], "series": [{"values": [1]}]},
                                   "steps": [{"reveal": 5}]}))
        try:
            server.render_json_page(bad)
            reasons.append("reveal beyond categories accepted")
        except ValueError:
            pass

    # schema file is valid JSON and the samples carry the right format
    json.loads((ROOT / "canvas-content.schema.json").read_text(encoding="utf-8"))
    for name in ("tcp-handshake.content.json", "agent-message.content.json", "chart-demo.content.json",
                 "autoresearch.content.json", "content-only-autoresearch.example.json"):
        check(json.loads((ROOT / name).read_text())["format"] == "canvas-content/v0", f"{name} format not migrated")

    # every source renders
    for name in ("tcp-handshake.content.json", "agent-message.content.json", "chart-demo.content.json",
                 "autoresearch.content.json", "autoresearch.md", "sample.md", "llm-train.md"):
        try:
            page = server.render_page(ROOT / name)
            raw_theme = json.loads((ROOT / name).read_text(encoding="utf-8")).get("theme") if name.endswith(".json") else None
            want = raw_theme or server.THEMES[0]
            check(f'data-theme="{want}"' in page, f"{name}: page theme is not {want}")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"{name}: render failed: {exc}")
    check('data-theme="sketch"' in server.render_page(ROOT / "tcp-handshake.content.json", "sketch"), "theme override ignored")
    check('data-theme="book"' in server.render_page(ROOT / "tcp-handshake.content.json", "paper"), "unknown theme should fall back to the file / default")


def minimal_fill_checks():
    """Token-minimal fill: no edges, short keys, defaults applied."""
    d = server.load_content_json(ROOT / "minimal.content.json")
    check(len(d["edges"]) == 3 and len(d["steps"]) == 3, "minimal: steps should invent edges")
    check(d["theme"] == "book" and "look" not in d and "motion" not in d, "minimal defaults")
    check([g["id"] for g in d["groups"]] == ["ask", "body", "end"], "minimal groups (总-分-总)")
    # dense teaching-board demos (A–F); memory+transformer=lesson step stage
    for demo, want_disp in (
        ("claude-code-memory.content.json", "sheet"),
        ("transformer.content.json", "sheet"),
    ):
        dd = server.load_content_json(ROOT / demo)
        check(boards.has_boards(dd), f"{demo}: expected blocks")
        letters = boards.board_letters(dd)
        check(list(letters.values()) == list("ABCDEF"), f"{demo}: letters {letters}")
        check(str(dd.get("orientation") or "landscape") == "landscape", f"{demo}: want landscape")
        check(str(dd.get("display") or "sheet") == want_disp, f"{demo}: want {want_disp} display")
        play = boards.board_play(dd)
        if want_disp == "lesson":
            check(len(play["steps"]) >= 6, f"{demo}: lesson needs >=6 board steps, got {len(play['steps'])}")
            check(all(st.get("intra") for st in play["steps"]), f"{demo}: steps missing intra")
            # block kinds present in content (not only walk kinds)
            bk = set()
            for g in dd.get("groups") or []:
                for b in g.get("blocks") or []:
                    if isinstance(b, dict):
                        bk.add(str(b.get("type") or ""))
            check({"chips", "bars", "callout"} <= bk and (bk & {"flowchart", "sequence", "mermaid"}), f"{demo}: lesson missing visual blocks {bk}")
        else:
            check(len(play["steps"]) >= 18, f"{demo}: need intra steps, got {len(play['steps'])}")
            check(all(st.get("intra") for st in play["steps"]), f"{demo}: steps missing intra")
            kinds = {st["intra"]["kind"] for st in play["steps"]}
            check(kinds & {"bars", "callout"} and kinds & {"flowchart", "sequence", "code", "chips"}, f"{demo}: missing visual intra kinds {kinds}")
        for th in server.THEMES:
            h = server.render_page(ROOT / demo, th)
            check(f'data-theme="{th}"' in h, f"{demo}: theme {th} missing")
            check('"boards": true' in h or '"boards":true' in h, f"{demo}: boards mode missing")
            check("paintBoardIntra" in h and "cv-tr-focus" in h, f"{demo}: intra-block motion missing")
            check("Rules the agent works under" not in h, f"{demo}: bottom rules table must not render")
            check('data-orientation="landscape"' in h, f"{demo}: landscape orientation attr missing")
            check(f'data-display="{want_disp}"' in h, f"{demo}: {want_disp} display attr missing")
            if want_disp == "sheet":
                check("--cv-cols" in h or "data-cols=" in h, f"{demo}: sheet cols missing")
            else:
                check("cv-lesson" in h and "cv-tok" in h, f"{demo}: lesson stage/chips missing")
            check("Rules the agent works under" not in h, f"{demo}: bottom rules must stay off")
            check('id="toolbar"' in h and 'data-theme-pick="sketch"' in h, f"{demo}: top toolbar missing")
        # the same file still opens one board at a time when asked
        lesson = server.render_page(ROOT / demo, None, "page", "lesson")
        check('data-display="lesson"' in lesson and "cv-lesson" in lesson, f"{demo}: ?display=lesson not honoured")
        check(len(boards.board_play(server.load_content_json(ROOT / demo) | {"display": "lesson"})["steps"]) >= 6,
              f"{demo}: lesson view needs one step per board")

    html = server.render_page(ROOT / "minimal.content.json", "book")
    check('data-theme="book"' in html, "minimal page lost its theme")
    # 3b1b still not a page theme choice
    check("3b1b" not in server.THEMES, "3b1b leaked into page themes")


def playback_checks():
    check(server.normalize_playback(None) == {"autoplay": False, "interval_ms": 1800, "loop": False}, "playback defaults wrong")
    check(server.normalize_playback({"autoplay": True, "interval_ms": 900, "loop": True}) == {"autoplay": True, "interval_ms": 900, "loop": True}, "playback not read")
    check(server.normalize_playback({"interval_ms": 5})["interval_ms"] >= 300, "playback interval not clamped")
    check(server.normalize_playback({"autoplay": "yes"})["autoplay"] is False, "playback accepts non-bool autoplay")
    data = json.loads((ROOT / "tcp-handshake.content.json").read_text(encoding="utf-8"))
    data["playback"] = {"autoplay": True, "interval_ms": 1200, "loop": True}
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "pb.content.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        html = server.render_page(p)
    check('"playback": {"autoplay": true, "interval_ms": 1200, "loop": true}' in html, "playback field not passed to the page")
    check('id="play"' in server.render_page(ROOT / "tcp-handshake.content.json"), "play button missing")


def export_checks():
    data = server.load_content_json(ROOT / "tcp-handshake.content.json")
    canvas = export.to_obsidian(data)
    ids = {n["id"] for n in canvas["nodes"]}
    for n in canvas["nodes"]:
        check(all(isinstance(n.get(k), (int, float)) for k in ("x", "y", "width", "height")), f"obsidian node {n['id']} lacks geometry")
        check(n["type"] in {"text", "group"}, "obsidian node type")
    for e in canvas["edges"]:
        check(e["fromNode"] in ids and e["toNode"] in ids, f"obsidian edge {e['id']} dangles")
        check(e["fromSide"] in {"top", "right", "bottom", "left"} and e["toSide"] in {"top", "right", "bottom", "left"}, "obsidian side")
    check(len(canvas["edges"]) == 4, "obsidian should keep 4 visible edges, drop invisible ones")

    scene = export.to_excalidraw(data, "sketch")
    check(scene["type"] == "excalidraw" and scene["version"] == 2, "excalidraw header")
    els = {e["id"]: e for e in scene["elements"]}
    check(len(els) == len(scene["elements"]), "excalidraw duplicate ids")
    for e in scene["elements"]:
        if e["type"] == "arrow":
            check(e["startBinding"]["elementId"] in els and e["endBinding"]["elementId"] in els, "arrow binding dangles")
        if e["type"] == "text" and e.get("containerId"):
            check(e["containerId"] in els, "text container dangles")
        for b in e.get("boundElements") or []:
            check(b["id"] in els, "boundElements dangles")
    check(all(e["roughness"] == 1 for e in scene["elements"] if e["type"] == "rectangle"), "sketch theme not passed to excalidraw")
    clean = export.to_excalidraw(data, "product")
    check(all(e["roughness"] == 0 for e in clean["elements"] if e["type"] == "rectangle"), "product excalidraw should draw clean lines")
    # page themes: book / product / sketch; 3b1b = video only
    check(tuple(server.THEMES) == ("book", "product", "sketch") and "3b1b" not in server.THEMES, f"page themes are {server.THEMES}")
    page = server.render_page(ROOT / "tcp-handshake.content.json", "3b1b")
    check('data-theme="product"' in page and 'data-mode="page"' in page, "?theme=3b1b should fall back to the file's theme on the page")
    check("data-theme=\"3b1b\"" not in page and "value=\"3b1b\"" not in page, "page still offers 3b1b theme")
    check('value="3b1b"' not in page and 'id="overview"' not in page, "page still offers 3b1b / 全景")
    import contextlib
    import io
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            export.main(["tcp-handshake.content.json", "--to", "html", "--theme", "3b1b"])
        reasons.append("export --to html --theme 3b1b was accepted")
    except SystemExit as exc:
        check(exc.code != 0, "export --theme 3b1b should be rejected")
    vpage = server.render_page(ROOT / "tcp-handshake.content.json", None, "video")
    check('data-theme="3b1b"' in vpage and 'data-mode="video"' in vpage and "cvVideo" in vpage, "video renderer page missing 3b1b look / cvVideo")

    # partition letters: A, B, C ... in declaration order; label/badge overrides
    exp = {"autoresearch.content.json": {"question": "A", "premises": "B", "mechanism": "C", "conclude": "D"},
           "tcp-handshake.content.json": {"client": "A", "server": "B"},
           "agent-message.content.json": {"ask": "A", "clarify": "B", "deepen": "C", "synthesize": "D"}}
    for name, want_l in exp.items():
        d = server.load_content_json(ROOT / name)
        got = {g["id"]: g["letter"] for g in layout.resolve_groups(d) if g["id"] != "_loose"}
        check(got == want_l, f"{name}: letters {got}, want {want_l}")
        mm = server.content_to_mermaid(d)
        for gid, letter in want_l.items():
            check(f'subgraph grp_{gid}["{letter} · ' in mm, f"{name}: mermaid subgraph {gid} lacks letter {letter}")
        labels = sorted(n["label"][0] for n in export.to_obsidian(d)["nodes"] if n["type"] == "group")
        check(labels == sorted(want_l.values()), f"{name}: obsidian group labels lack letters {labels}")
        names = sorted(e["name"][0] for e in export.to_excalidraw(d, "book")["elements"] if e["type"] == "frame")
        check(names == sorted(want_l.values()), f"{name}: excalidraw frame names lack letters {names}")
    check(layout.page_letters(server.load_content_json(ROOT / "chart-demo.content.json")) == {"data": "A", "explain": "B"}, "chart regions not lettered A, B")
    d = json.loads((ROOT / "tcp-handshake.content.json").read_text(encoding="utf-8"))
    d["lanes"][1]["badge"] = "S"
    check([g["letter"] for g in layout.resolve_groups(d)] == ["A", "S"], "lane badge override ignored")
    d = server.load_content_json(ROOT / "autoresearch.content.json")
    d["groups"][0]["label"] = "F"
    check(layout.resolve_groups(d)[0]["letter"] == "F", "group label override ignored")
    check(layout.letter_for(25) == "Z" and layout.letter_for(26) == "AA", "letters past Z")

    cd = server.load_content_json(ROOT / "chart-demo.content.json")
    md = [n for n in export.to_obsidian(cd)["nodes"] if n["id"] == "chart"]
    check(md and "| W3 | 16 | 9 |" in md[0]["text"] and "示例数据" in md[0]["text"], "chart not exported as a markdown table")
    am = server.load_content_json(ROOT / "agent-message.content.json")
    check(len(export.to_obsidian(am)["edges"]) == 8, "agent-message obsidian export should keep 8 messages")

    # groups: every sample is split into titled regions, and exports carry them
    want = {"autoresearch.content.json": 4, "tcp-handshake.content.json": 2,
            "agent-message.content.json": 4, "chart-demo.content.json": 2}
    for name, count in want.items():
        d = server.load_content_json(ROOT / name)
        gs = d["groups"] if name == "chart-demo.content.json" else layout.resolve_groups(d)
        real = [g for g in gs if not str(g["id"]).startswith("_")]
        check(len(real) == count, f"{name}: {len(real)} groups, want {count}")
        check(all(str(g.get("title") or "").strip() for g in real), f"{name}: a group has no title")
        if name == "chart-demo.content.json":
            continue
        node_ids = {str(n["id"]) for n in d["nodes"]}
        for g in real:
            check(g["members"] and set(g["members"]) <= node_ids, f"{name}: group {g['id']} members invalid")
        obs = export.to_obsidian(d)
        groups = [n for n in obs["nodes"] if n["type"] == "group"]
        check(len(groups) == count and all(n.get("label") for n in groups), f"{name}: obsidian lacks {count} labelled group nodes")
        for n in obs["nodes"]:
            if n["type"] != "text":
                continue
            inside = [gn for gn in groups if gn["x"] <= n["x"] and gn["y"] <= n["y"]
                      and n["x"] + n["width"] <= gn["x"] + gn["width"] and n["y"] + n["height"] <= gn["y"] + gn["height"]]
            check(inside, f"{name}: obsidian node {n['id']} outside every group")
        ex = export.to_excalidraw(d, "book")
        frames = [e for e in ex["elements"] if e["type"] == "frame"]
        check(len(frames) == count and all(f.get("name") for f in frames), f"{name}: excalidraw lacks {count} named frames")
        fids = {f["id"] for f in frames}
        check(all(e.get("frameId") in fids for e in ex["elements"] if e["type"] == "rectangle" or e["type"] == "diamond"),
              f"{name}: excalidraw shapes not inside a frame")
        mm = server.content_to_mermaid(d)
        check(mm.count("subgraph ") == count, f"{name}: mermaid has {mm.count('subgraph ')} subgraphs, want {count}")




async def board_text_clip_checks(browser, base, check):
    """Teaching boards must not clip sample text (scrollWidth <= clientWidth; FO fit)."""
    page = await browser.new_page(viewport={"width": 1400, "height": 1000})
    try:
        for src in ("claude-code-memory.content.json", "transformer.content.json"):
            await page.goto(base + f"/?src={src}&theme=book", wait_until="networkidle")
            await page.wait_for_selector("body[data-renderer='boards']", timeout=20000)
            await page.wait_for_function("() => window.canvasPlayer && canvasPlayer.total >= 6", timeout=20000)
            await page.wait_for_timeout(600)
            total = await page.evaluate("() => canvasPlayer.total")
            want = 6 if src.startswith(("transformer", "claude-code-memory")) else 18
            check(total >= want, f"{src}: expected steps for clip check (got {total}, want >={want})")
            await page.evaluate("() => canvasPlayer.go(canvasPlayer.total)")
            await page.wait_for_timeout(900)
            report = await page.evaluate("""() => {
              const domClips = [];
              const sels = ['.cv-callout p','.cv-board-title','.cv-bar-label','.cv-bar-val','.cv-table th','.cv-table td','.cv-li','.cv-code-line'];
              for (const s of sels) {
                document.querySelectorAll(s).forEach((el) => {
                  if (el.closest('[hidden]') || el.offsetParent === null) return;
                  if (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 2) {
                    domClips.push({s, t:(el.textContent||'').trim().slice(0,40), sw:el.scrollWidth, cw:el.clientWidth});
                  }
                });
              }
              const foClips = [];
              document.querySelectorAll('.cv-md foreignObject').forEach((fo) => {
                const box = fo.querySelector('div,span,p');
                if (!box) return;
                const w = Number(fo.getAttribute('width') || 0);
                if (box.scrollWidth > w + 2) foClips.push({t:(box.textContent||'').trim().slice(0,40), sw:box.scrollWidth, w});
              });
              return {domClips, foClips, boards: document.querySelectorAll('.cv-board').length};
            }""")
            check(report["boards"] >= 6, f"{src}: expected teaching boards")
            check(len(report["domClips"]) == 0, f"{src}: DOM text clipped {report['domClips'][:3]}")
            check(len(report["foClips"]) == 0, f"{src}: mermaid FO clipped {report['foClips'][:3]}")
    finally:
        await page.close()


async def browser_checks(base: str):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 960, "height": 1000})
        await pg.goto(base + "/?src=tcp-handshake.content.json")
        await pg.wait_for_selector("body[data-renderer]", timeout=20000)
        step = lambda: pg.evaluate("Number(document.body.dataset.step)")  # noqa: E731
        bg = await pg.evaluate("getComputedStyle(document.body).backgroundColor")
        check(bg == "rgb(245, 246, 248)", f"tcp page background is {bg}, not the product theme")
        check(await step() == 0, "initial step is not 0")
        check(await pg.evaluate("document.body.dataset.total") == "4", "tcp total steps != 4")
        check(await pg.is_disabled("#prev"), "上一步 enabled at step 0")
        await pg.click("#next")
        check(await step() == 1, "下一步 did not advance to 1")
        check(await pg.locator("g.node.cv-current[data-node-id=s_rcvd]").count() == 1, "SYN_RCVD not bold after step 1")
        check(await pg.locator('path.cv-edge.cv-flow[data-edge-key="L_c_sent_s_rcvd_0"]').count() == 1, "SYN edge not flowing")
        # click the hinted next edge (SYN-ACK) on the diagram itself
        await pg.wait_for_timeout(200)
        await pg.locator('.edgeLabel.cv-next').first.click()
        check(await step() == 2, "clicking the hinted edge did not advance")
        await pg.click("#next")
        check(await step() == 3, "third 下一步 did not reach 3")
        check(await pg.locator("g.node.cv-current[data-node-id=s_est]").count() == 1, "server ESTABLISHED not bold after ACK")
        await pg.click("#prev")
        check(await step() == 2, "上一步 did not go back")
        await pg.click("#reset")
        check(await step() == 0, "重来 did not reset to 0")
        for _ in range(4):
            await pg.click("#next")
        check(await step() == 4 and await pg.is_disabled("#next"), "下一步 not disabled at the end")
        await pg.keyboard.press("r")
        check(await step() == 0, "r key did not reset")
        await pg.click('[data-theme-pick="sketch"]')
        font = await pg.evaluate("getComputedStyle(document.body).fontFamily")
        check("Virgil" in font, "sketch theme switch did not apply")
        await pg.close()

        pg = await b.new_page()
        await pg.goto(base + "/?src=tcp-handshake.content.json&theme=book")
        bg = await pg.evaluate("getComputedStyle(document.body).backgroundColor")
        check(bg == "rgb(246, 242, 234)", f"book theme background is {bg}")
        await pg.close()

        # agent messages: packet runs along each message, body shows in the detail box
        pg = await b.new_page(viewport={"width": 1100, "height": 1000})
        await pg.goto(base + "/?src=agent-message.content.json")
        await pg.wait_for_selector("body[data-renderer]", timeout=20000)
        check(await pg.locator("#diagram g.cv-group").count() == 4, "agent page should have 4 pedagogical regions")
        check(await pg.is_hidden("#detail"), "detail box visible at step 0")
        await pg.click("#next")
        await pg.click("#next")
        check(await pg.evaluate("document.body.dataset.step") == "2", "agent page did not reach step 2")
        check("coder" in (await pg.text_content("#detail") or ""), "step 2 detail does not show the message body")
        check(await pg.locator("g.node.cv-current[data-node-id=c_task]").count() == 1, "Coder not bold after task message")
        check(await pg.evaluate("Number(document.getElementById('cv-packet').getAttribute('r'))") > 0, "packet dot not moving")
        await pg.close()

        # chart: steps reveal groups, hover/click highlight, in every theme
        for theme in server.THEMES:
            pg = await b.new_page(viewport={"width": 960, "height": 1000})
            await pg.goto(base + f"/?src=chart-demo.content.json&theme={theme}")
            await pg.wait_for_selector("body[data-renderer]", timeout=20000)
            chart = pg.locator("#chart")
            rv = lambda: chart.get_attribute("data-reveal")  # noqa: E731
            check(await rv() == "0", f"{theme}: chart should start with 0 groups")
            check(await pg.locator(".cv-bar:not(.cv-hidden)").count() == 0, f"{theme}: bars visible at step 0")
            await pg.click("#next")
            check(await rv() == "2", f"{theme}: step 1 should reveal 2 groups")
            check(await pg.locator(".cv-bar:not(.cv-hidden)").count() == 4, f"{theme}: step 1 should show 4 bars")
            await pg.click("#next")
            check(await chart.get_attribute("data-highlight") == "2", f"{theme}: step 2 should highlight W3")
            check("16" in (await pg.text_content("#chart-readout") or ""), f"{theme}: readout lacks W3 value")
            await pg.hover('.cv-hitcol[data-cat="0"]')
            check(await chart.get_attribute("data-highlight") == "0", f"{theme}: hover did not highlight W1")
            await pg.click('.cv-hitcol[data-cat="1"]')
            await pg.mouse.move(5, 5)
            check(await chart.get_attribute("data-highlight") == "1", f"{theme}: click did not pin W2")
            await pg.hover('.cv-hitcol[data-cat="5"]')
            check(await chart.get_attribute("data-highlight") == "1", f"{theme}: hidden group reacted to hover")
            fill = await pg.evaluate("getComputedStyle(document.querySelector('.cv-bar.cv-s0')).fill")
            check(fill not in ("", "none", "rgb(0, 0, 0)") or theme == "book", f"{theme}: series colour not themed ({fill})")
            await pg.click("#reset")
            check(await rv() == "0" and await chart.get_attribute("data-highlight") == "", f"{theme}: 重来 did not clear chart")
            await pg.close()

        # groups visible with titles in every theme
        want = {"autoresearch.content.json": 4, "tcp-handshake.content.json": 2,
                "agent-message.content.json": 4, "chart-demo.content.json": 2}
        for theme in server.THEMES:
            for src, count in want.items():
                pg = await b.new_page(viewport={"width": 1280, "height": 1000})
                await pg.goto(base + f"/?src={src}&theme={theme}")
                await pg.wait_for_selector("body[data-renderer]", timeout=20000)
                info = await pg.evaluate("""() => {
                  const gs = [...document.querySelectorAll('#diagram g.cv-group, section.cv-region')];
                  return gs.map(g => {
                    const box = g.querySelector('.cv-group-box') || g;
                    const cs = getComputedStyle(box);
                    const t = g.querySelector('.cv-group-title, .cv-region-title');
                    const r = box.getBoundingClientRect();
                    return {title: t ? t.textContent.trim() : '', titleVis: t ? getComputedStyle(t).visibility !== 'hidden' && Number(getComputedStyle(t).opacity) > 0 : false,
                            stroke: cs.stroke !== 'none' ? cs.stroke : cs.borderTopColor, bw: cs.strokeWidth || cs.borderTopWidth,
                            fill: cs.fill !== 'none' ? cs.fill : cs.backgroundColor,
                            op: Number(cs.opacity), w: r.width, h: r.height};
                  });
                }""")
                tag = f"{theme}/{src}"
                check(len(info) == count, f"{tag}: {len(info)} regions on page, want {count}")
                for g in info:
                    check(g["title"] and g["titleVis"], f"{tag}: region without visible title")
                    check(g["op"] > 0 and g["w"] > 40 and g["h"] > 40, f"{tag}: region box invisible {g}")
                    visible = g["stroke"] not in ("", "none", "rgba(0, 0, 0, 0)") or g["fill"] not in ("", "none", "rgba(0, 0, 0, 0)")
                    check(visible, f"{tag}: region has neither stroke nor fill {g}")
                letters = await pg.evaluate("""() => [...document.querySelectorAll('#diagram g.cv-group[data-letter], section.cv-region')]
                    .map(g => [g.getAttribute('data-letter'), (g.querySelector('.cv-group-letter, .cv-region-badge') || {}).textContent || '',
                               !!(g.querySelector('.cv-group-badge') && g.querySelector('.cv-group-badge').getBoundingClientRect().width > 8)])""")
                want_letters = [chr(65 + i) for i in range(count)]
                check(sorted(l[0] for l in letters) == want_letters, f"{tag}: region letters {letters}, want {want_letters}")
                check(all(l[0] == l[1].strip() and l[2] for l in letters), f"{tag}: letter badge not drawn {letters}")
                await pg.close()

        # sheet: drawing frame with rulers + title strip; one board opens on its own and closes on Escape
        pg = await b.new_page(viewport={"width": 1500, "height": 1000})
        await pg.goto(base + "/?src=transformer.content.json")
        await pg.wait_for_selector("body[data-renderer='boards']", timeout=20000)
        frame = await pg.evaluate("""() => ({
          rulers: document.querySelectorAll('.cv-drawing .cv-ruler').length,
          cells: [...document.querySelectorAll('.cv-titleblock .cv-tb-cell span')].map(s => s.textContent),
          sticky: getComputedStyle(document.getElementById('toolbar')).position,
          inBar: !!document.querySelector('#toolbar #next') && !!document.querySelector('#toolbar #cap'),
          views: [...document.querySelectorAll('#views [data-view]')].map(a => a.dataset.view)
        })""")
        check(frame["rulers"] == 4, f"sheet: rulers missing {frame}")
        check("标题" in frame["cells"] and "步数" in frame["cells"] and len(frame["cells"]) >= 4, f"sheet: title strip lacks meta {frame['cells']}")
        check(frame["sticky"] == "sticky" and frame["inBar"], f"toolbar not sticky on top / controls not in it {frame}")
        check(frame["views"] == ["sheet", "deck", "lesson"], f"view pills {frame['views']}")
        await pg.hover(".cv-board[data-letter='B']")
        await pg.click(".cv-board[data-letter='B'] .cv-board-zoom")
        check(await pg.evaluate("!!document.querySelector('dialog.cv-zoom[open] .cv-board-zoomed')"), "board did not open on its own")
        s0 = await step()
        await pg.keyboard.press("ArrowRight")
        check(await step() == s0, "arrow keys stepped the sheet behind an open board")
        await pg.keyboard.press("Escape")
        check(await pg.evaluate("!document.querySelector('dialog.cv-zoom[open]')"), "Escape did not close the open board")
        await pg.close()

        # toolbar keeps one height while stepping; Space on a focused pill presses the pill, not play
        pg = await b.new_page(viewport={"width": 1500, "height": 1000})
        await pg.goto(base + "/?src=agent-message.content.json")
        await pg.wait_for_selector("body[data-renderer]", timeout=20000)
        heights = set()
        for _ in range(5):
            await pg.click("#next")
            heights.add(round(await pg.evaluate("document.getElementById('toolbar').getBoundingClientRect().height")))
        check(len(heights) == 1, f"toolbar height changes while stepping {sorted(heights)}")
        await pg.focus('[data-theme-pick="sketch"]')
        await pg.keyboard.press(" ")
        check(not await pg.evaluate("window.canvasPlayer.playing") and await pg.evaluate("document.body.dataset.theme") == "sketch",
              "Space on a focused theme pill started playback instead of switching theme")
        await pg.close()

        # exported single file: no server, so 逐页 is hidden and ?theme is read in the page
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "t.html"
            out.write_text(server.render_page(ROOT / "transformer.content.json"), encoding="utf-8")
            pg = await b.new_page(viewport={"width": 1500, "height": 1000})
            await pg.goto(out.as_uri() + "?theme=product")
            await pg.wait_for_selector("body[data-renderer]", timeout=20000)
            check(await pg.evaluate("document.body.dataset.theme") == "product", "exported page ignores ?theme")
            check(await pg.is_hidden('#views [data-view="lesson"]'), "exported page offers 逐页, which needs the server")
            await pg.click('#views [data-view="deck"]')
            check(await pg.evaluate("document.body.dataset.display") == "deck", "sheet -> deck did not switch in place")
            await pg.close()

        # video: no toolbar; the caption sits after the stage
        pg = await b.new_page(viewport={"width": 1920, "height": 1080})
        await pg.goto(base + "/video?src=tcp-handshake.content.json")
        await pg.wait_for_function("window.cvVideo", timeout=20000)
        v = await pg.evaluate("""() => ({ bar: getComputedStyle(document.getElementById('toolbar')).display,
          after: document.getElementById('stage').nextElementSibling === document.getElementById('caption') })""")
        check(v["bar"] == "none" and v["after"], f"video page layout {v}")
        await pg.close()

        # auto-play: button + Space toggle, ?autoplay=1, manual step pauses, stops at the end
        playing = lambda pg: pg.evaluate("window.canvasPlayer.playing")  # noqa: E731
        for theme in server.THEMES:
            pg = await b.new_page(viewport={"width": 1280, "height": 1000})
            await pg.goto(base + f"/?src=tcp-handshake.content.json&theme={theme}")
            await pg.wait_for_selector("body[data-renderer]", timeout=20000)
            check(await pg.evaluate("window.canvasPlayer.interval") == 1800, f"{theme}: default interval not 1.8s")
            check(not await playing(pg), f"{theme}: plays without being asked")
            await pg.click("#play")
            check(await playing(pg) and "暂停" in (await pg.text_content("#play") or ""), f"{theme}: 播放 button did not start")
            await pg.wait_for_timeout(600)
            check(int(await pg.evaluate("document.body.dataset.step")) >= 1, f"{theme}: autoplay did not take the first step")
            await pg.keyboard.press("Space")
            check(not await playing(pg), f"{theme}: Space did not pause")
            s0 = await pg.evaluate("document.body.dataset.step")
            await pg.wait_for_timeout(2000)
            check(await pg.evaluate("document.body.dataset.step") == s0, f"{theme}: still stepping after pause")
            await pg.keyboard.press("Space")
            check(await playing(pg), f"{theme}: Space did not resume")
            await pg.click("#next")
            check(not await playing(pg), f"{theme}: 下一步 did not pause autoplay")
            await pg.click("#play")
            await pg.click("#prev")
            check(not await playing(pg), f"{theme}: 上一步 did not pause autoplay")
            await pg.close()

        pg = await b.new_page(viewport={"width": 1280, "height": 1000})
        await pg.goto(base + "/?src=tcp-handshake.content.json&autoplay=1")
        await pg.wait_for_selector("body[data-renderer]", timeout=20000)
        await pg.wait_for_timeout(900)
        check(await playing(pg), "?autoplay=1 did not start playback")
        await pg.wait_for_function("Number(document.body.dataset.step) === 4", timeout=12000)
        await pg.wait_for_timeout(300)
        check(not await playing(pg), "autoplay did not stop at the last step (loop off)")
        await pg.close()

        # step badges are drawn on lane/group diagrams
        pg = await b.new_page(viewport={"width": 1280, "height": 1000})
        await pg.goto(base + "/?src=autoresearch.content.json")
        await pg.wait_for_selector("body[data-renderer]", timeout=20000)
        check(await pg.locator("#diagram .cv-step-badge").count() >= 10, "autoresearch: step badges missing")
        await pg.close()

        # page: no 3b1b in the selector, motion = edge draw-on for the current step
        errors = []
        pg = await b.new_page(viewport={"width": 1280, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(base + "/?src=tcp-handshake.content.json&theme=3b1b")
        await pg.wait_for_selector("body[data-renderer]", timeout=20000)
        opts = await pg.evaluate("[...document.querySelectorAll('[data-theme-pick]')].map(o => o.dataset.themePick)")
        check(opts == list(server.THEMES), f"theme selector offers {opts}")
        check(await pg.evaluate("document.body.dataset.theme") == "product", "?theme=3b1b not ignored on the page")
        await pg.click("#next")
        anim = await pg.evaluate("""() => { const p = document.querySelector('path.cv-edge.cv-flow');
          return p ? [getComputedStyle(p).animationName, p.getAttribute('pathLength')] : null; }""")
        check(anim and anim[0] == "cv-edge-draw" and anim[1] == "1", f"current edge does not draw on: {anim}")
        await pg.wait_for_timeout(900)
        check(await pg.evaluate("document.querySelector('path.cv-edge[pathLength]') === null"), "pathLength left on edges after draw-on")
        await pg.close()

        # all samples walk to the end in every page theme without page errors
        for theme in server.THEMES:
            for src in ("tcp-handshake.content.json", "agent-message.content.json", "chart-demo.content.json", "autoresearch.content.json", "autoresearch.md"):
                pg = await b.new_page(viewport={"width": 1280, "height": 1000})
                pg.on("pageerror", lambda e, src=src: errors.append(f"{src}: {e}"))
                await pg.goto(base + f"/?src={src}&theme={theme}")
                await pg.wait_for_selector("body[data-renderer]", timeout=20000)
                total = int(await pg.evaluate("document.body.dataset.total"))
                for _ in range(total):
                    await pg.click("#next")
                check(int(await pg.evaluate("document.body.dataset.step")) == total and total > 0, f"{theme}: {src} did not reach the end")
                await pg.close()

        # video renderer (/video): 3b1b look, deterministic render(t), camera follows each step
        def parse_box(t):
            return [float(x) for x in (t or "").split()]

        for src in ("tcp-handshake.content.json", "autoresearch.content.json", "agent-message.content.json"):
            pg = await b.new_page(viewport={"width": 1920, "height": 1080})
            pg.on("pageerror", lambda e, src=src: errors.append(f"video {src}: {e}"))
            await pg.goto(base + f"/video?src={src}")
            await pg.wait_for_selector("body[data-renderer]", timeout=20000)
            check(await pg.evaluate("document.body.dataset.theme") == "3b1b", f"video {src}: not 3b1b")
            bg = await pg.evaluate("getComputedStyle(document.body).backgroundColor")
            rgb = [int(x) for x in bg[bg.index("(") + 1:bg.index(")")].split(",")[:3]]
            check(max(rgb) < 60, f"video {src}: background not dark: {bg}")
            check(await pg.is_hidden(".bar"), f"video {src}: controls visible in video")
            check(await pg.evaluate("cvVideo.init()"), f"video {src}: init failed")
            fill = await pg.evaluate("getComputedStyle(document.querySelector('#diagram g.node rect, #diagram g.node polygon')).fill")
            check(fill in ("transparent", "rgba(0, 0, 0, 0)", "none"), f"video {src}: nodes not outline-only: {fill}")
            T = await pg.evaluate("cvVideo.timing")
            n = int(await pg.evaluate("document.body.dataset.total"))
            dur = await pg.evaluate("cvVideo.duration")
            check(abs(dur - (T["intro"] + n * T["step"] + T["outro"])) < 1e-6, f"video {src}: duration mismatch")
            await pg.evaluate("cvVideo.render(0.2)")
            full = parse_box(await pg.get_attribute("#diagram", "data-camera"))
            check(await pg.get_attribute("#diagram", "data-camera-mode") == "overview", f"video {src}: t=0 not overview")
            cams = []
            for k in (1, 2, 3):
                t_end = T["intro"] + k * T["step"] - 0.05
                await pg.evaluate(f"cvVideo.render({t_end})")
                cams.append(parse_box(await pg.get_attribute("#diagram", "data-camera")))
                check(await pg.get_attribute("#diagram", "data-camera-mode") == "focus", f"video {src}: step {k} not focused")
                check(int(await pg.evaluate("document.body.dataset.step")) == k, f"video {src}: render(t) at step {k} wrong step")
                tag = (await pg.text_content("#cv-region-tag") or "").strip()
                check(len(tag) >= 2 and tag[0] in "ABCDEFGH", f"video {src}: current region tag missing at step {k} ({tag!r})")
            check(len({tuple(c) for c in cams}) == 3, f"video {src}: camera did not move per step {cams}")
            check(all(c[2] * c[3] < full[2] * full[3] * 1.5 for c in cams) or src == "tcp-handshake.content.json",
                  f"video {src}: camera did not zoom in")
            # edge draw-on: halfway through a step the current edge is partly drawn
            t_mid = T["intro"] + 0.34 * T["step"]
            await pg.evaluate(f"cvVideo.render({t_mid})")
            off = await pg.evaluate("""() => { const p = document.querySelector('path.cv-edge.cv-flow');
              return p ? [parseFloat(p.style.strokeDashoffset), p.getTotalLength()] : null; }""")
            check(off and 0 < off[0] < off[1], f"video {src}: edge not mid draw-on {off}")
            # deterministic: same t, same frame state
            await pg.evaluate("cvVideo.render(5.0)")
            c1 = await pg.get_attribute("#diagram", "data-camera")
            await pg.evaluate("cvVideo.render(1.0)")
            await pg.evaluate("cvVideo.render(5.0)")
            check(await pg.get_attribute("#diagram", "data-camera") == c1, f"video {src}: render(t) not deterministic")
            await pg.evaluate(f"cvVideo.render({dur})")
            check(parse_box(await pg.get_attribute("#diagram", "data-camera")) == full, f"video {src}: does not end on the overview")
            await pg.close()
        check(not errors, f"page errors: {errors}")

        pg = await b.new_page()
        await pg.goto(base + "/?src=sample.md")
        await pg.wait_for_selector("body[data-renderer]", timeout=20000)
        await pg.click("#next")
        check(await pg.evaluate("document.body.dataset.step") == "1", "markdown page does not step")
        await pg.close()
        
        await board_text_clip_checks(b, base, check)
        await b.close()


def video_checks():
    """export.py --to video: real mp4, 1920x1080, 30 fps, duration = renderer timeline."""
    import subprocess
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "tcp.mp4"
        try:
            export.to_video(ROOT / "tcp-handshake.content.json", out, log=None)
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"video export failed: {exc}")
            return
        check(out.exists() and out.stat().st_size > 50_000, "video export produced no mp4")
        probe = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=codec_name,width,height,r_frame_rate,nb_frames,pix_fmt:format=duration,format_name", "-of", "json", str(out)],
            capture_output=True, text=True, check=True).stdout)
        st, fm = probe["streams"][0], probe["format"]
        n_steps = len(server.load_content_json(ROOT / "tcp-handshake.content.json")["steps"])
        want = 1.8 + n_steps * (1.8 + 1.0) + 2.4
        check((st["width"], st["height"]) == (1920, 1080), f"video size {st['width']}x{st['height']}")
        check(st["r_frame_rate"] == "30/1", f"video fps {st['r_frame_rate']}")
        check(st["codec_name"] == "h264" and st["pix_fmt"] == "yuv420p" and "mp4" in fm["format_name"], f"video codec {st}")
        check(abs(float(fm["duration"]) - want) < 0.1, f"video duration {fm['duration']} != {want:.2f}")
        check(int(st["nb_frames"]) == round(want * 30), f"video frames {st['nb_frames']}")
    try:
        export.to_video(ROOT / "chart-demo.content.json", Path(tempfile.gettempdir()) / "never.mp4", log=None)
        reasons.append("video export of a chart-only file should refuse (no diagram)")
    except RuntimeError:
        check(True, "")


def main() -> None:
    sources = sorted(p for p in ROOT.iterdir() if p.suffix in {".md", ".json"} and p.is_file())
    before = digest(sources)
    static_checks()
    minimal_fill_checks()
    playback_checks()
    export_checks()

    httpd = server.make_server("127.0.0.1", 0)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        req = urllib.request.Request(base + "/writeback?src=sample.md", data=b"{}", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            reasons.append("POST /writeback answered 2xx")
        except urllib.error.HTTPError as exc:
            check(exc.code in (404, 405, 501), f"POST /writeback returned {exc.code}")
        asyncio.run(browser_checks(base))
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"browser check crashed: {exc}")
    finally:
        httpd.shutdown()

    video_checks()
    check(digest(sources) == before, "a source file changed during verify (writeback?)")
    check(not (ROOT / "messages.log").exists(), "messages.log was created")

    if reasons:
        print("FAIL")
        for r in reasons:
            print(" -", r)
        sys.exit(1)
    print(f"PASS ({counted[0]} checks)")


if __name__ == "__main__":
    main()
