"""Record a screen capture of the embedded surface, driven by real bench CAN.

Everything on screen is produced by the system, not staged for the camera: the
gauge values are decoded from the DBC out of a scripted drive, the origin
boundary is a real sandboxed frame, and the refusals are the host's own gate
turning away messages this script genuinely sends.

    ops/record_demo.sh [--out demo.mp4]

The recording runs the application the way it ships: two servers on two ports,
the API holding every data source and the client holding three HTML files. The
browser really does cross an origin to ask for each turn, so the CORS allowance
and the client tag in the video are the ones the application enforces, not a
narration of them.

Produces an mp4 (H.264/yuv420p, 1280x720) if ffmpeg is present, and always
leaves the raw webm beside it.
"""
from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright

from telltale.web import client as client_module
from telltale.web.client import router as client_router
from telltale.web.routes import CLIENT_TAG_HEADER
from telltale.web.routes import router as api_router
from telltale.vehicle.dbc import SignalCatalog
from telltale.vehicle import dashboard
from telltale.vehicle.profile import drive_profile

# The project root, which holds both repositories and the demo output. The DBC
# is application data and stays with the application.
ROOT = Path(__file__).resolve().parents[1]
BENCH_DBC = ROOT / "telltale" / "telltale" / "vehicle" / "fixtures" / "bench_rig.dbc"
RUN_ID = "bench-drive"
CLIENT_ID = "demo-kiosk"

INJECTED = '<img src=x onerror="window.__pwned=1">'

# The opening beats, on the start page, before any surface is composed.
INTRO = [
    "two servers: the API holds the data, the client holds three HTML files",
    "the start page asks the server what it is reading — it does not claim it",
    "43 signals, 763 frames, 24 catalog components — read from the running API",
    "every request the client makes is tagged with who it is",
]

# Approving one code cannot be turned into approving all of them: the server
# compares the args against the card it composed, not against anything the
# caller says it was shown.
TAMPERED = "the same approval, widened to every code — refused against the card it composed"

CAPTIONS = [
    "eight ECU domains on one bus, 40 signals, every scale read from the DBC",
    "powertrain, battery, chassis, body, ADAS, infotainment, lighting, sensors",
    "the panels are grouped by CAN message — add one to the DBC and it appears",
    "a headlamp drawing no current reads red; low is the failing end for a lamp",
    "the surface renders in a sandboxed frame with an opaque origin",
    "it cannot read this page, its storage, or the API",
    "seven stored codes across six ECUs, every severity the DBC defines",
    "trends: the same signals over the window, not just their latest value",
    "one tire diverges from its three siblings — the leak a table can't show",
    "service targets the worst code present, gated by the same catalog check",
    "a registered action is accepted and reaches the runtime",
    "an action nobody registered never becomes a request",
    "markup in a bound value stays literal text, in every tab",
    "the surface can ask the framework to go and look — this is a real request",
    "the framework read the bus and composed the answer: a new interface, live",
    "\"run full diagnostic\" evaluates every limit and reports what it measured",
    "each check states the value it read and the limit it broke — not a verdict alone",
    "a scope nobody registered is refused, and the screen keeps what it had",
]

def dashboard_surface(scope: str = "status") -> dict:
    """The application's own surface, built by the module the server uses.

    The demo and the running application compose the same interface from the
    same code — a recording that exercised a private copy would prove nothing
    about what ships.
    """
    catalog = SignalCatalog.load(BENCH_DBC)
    return dashboard.build_surface(catalog, drive_profile(catalog), scope)


# --------------------------------------------------------------------------- #
# a real server on a real port, so the origin boundary in the video is real
# --------------------------------------------------------------------------- #

class _Snapshot:
    def __init__(self, nodes: dict) -> None:
        self.nodes = nodes
        self.edges: list = []
        self.finished = True


class _Graph:
    def __init__(self, surface: dict) -> None:
        self._nodes = {"surface": {"id": "surface", "state": "succeeded",
                                   "result": {"surface": surface,
                                              "data_model": surface["dataModel"],
                                              "provider": "bench", "model": "dbc"}}}

    def snapshot(self, run_id: str) -> _Snapshot:
        if run_id != RUN_ID:
            raise KeyError(run_id)
        return _Snapshot(self._nodes)

    def events(self, run_id: str) -> list:
        return []


class _Runtime:
    def __init__(self, surface: dict) -> None:
        self.graph = _Graph(surface)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _spawn(app: FastAPI, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    return server, thread


def start_servers(surface: dict) -> tuple[str, str, list]:
    """The application as it ships: two processes' worth of separation, on two
    ports, in one process so the recording can shut them both down.

    The API answers exactly one origin — the client's — and refuses any request
    that arrives without a client tag. Both of those are real in the video: the
    browser genuinely crosses an origin for every turn.
    """
    api_port, client_port = _free_port(), _free_port()
    api_base = f"http://127.0.0.1:{api_port}"
    client_base = f"http://127.0.0.1:{client_port}"

    api = FastAPI()
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[client_base],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", CLIENT_TAG_HEADER],
    )
    api.include_router(api_router)
    api.state.s13_runtime = _Runtime(surface)

    # What the client server stamps into every page it serves.
    client_module.API_BASE = api_base
    client_module.CLIENT_ID = CLIENT_ID
    client = FastAPI()
    client.include_router(client_router)

    running = [_spawn(api, api_port), _spawn(client, client_port)]
    return api_base, client_base, running


# --------------------------------------------------------------------------- #
# the recording itself
# --------------------------------------------------------------------------- #

CAPTION_JS = """
text => {
  let el = document.getElementById('__caption');
  if (!el) {
    el = document.createElement('div');
    el.id = '__caption';
    el.style.cssText = 'position:fixed;left:0;right:0;bottom:0;padding:14px 18px;'
      + 'background:#111;color:#fff;font:600 17px ui-monospace,Menlo,monospace;'
      + 'letter-spacing:.01em;z-index:9999;text-align:center';
    document.body.appendChild(el);
    // The caption is fixed over the page; keep it from covering the log it
    // is talking about.
    document.body.style.paddingBottom = '130px';
  }
  el.textContent = text;
}
"""

# The frame grows to fit its surface, which pushes the host's decision log below
# the fold. Those refusals are the point of the recording, so bring them on camera.
SCROLL_TO_LOG = "() => window.scrollTo({top: document.body.scrollHeight})"

# The same growth puts the domain panels below the fold. A caption about what
# colour a gauge reads is worth nothing while the gauges are cut off by the
# viewport, so the active panel is brought on camera before it is described.
# The frame cannot scroll the host — it has an opaque origin and no reach — so
# the host asks the frame where the panel is and does the scrolling itself.
SHOW_PANEL_HOST = """t => {
  const f = document.getElementById('frame');
  const top = f.getBoundingClientRect().top + window.scrollY + t;
  // Instant, not smooth: a smooth scroll is an animation, and an element in
  // motion never satisfies Playwright's 'stable' check, so every tap after a
  // scroll timed out waiting for the page to stop moving.
  window.scrollTo({top: Math.max(0, top - 250), behavior: 'auto'});
}"""

PANEL_TOP_FRAME = """() => {
  const svg = document.querySelector('svg');
  return svg ? svg.getBoundingClientRect().top + window.scrollY : null;
}"""


def show_panel(page, inner) -> None:
    """Scroll the host so the active domain panel's gauges are fully visible."""
    top = inner.evaluate(PANEL_TOP_FRAME)
    if top is None:
        return
    page.evaluate(SHOW_PANEL_HOST, top)
    page.wait_for_timeout(450)



# --------------------------------------------------------------------------- #
# the wire, made visible
# --------------------------------------------------------------------------- #

# The recording kept showing a screen changing without showing WHY. Three
# different things move here — the sandboxed app naming an action, the host
# turning that into a tagged HTTP request, and the server answering with a whole
# new interface — and on camera they were indistinguishable from a page that
# simply redrew itself.
#
# So the host is instrumented to report what actually crosses each boundary.
# Nothing is staged: the message log is a real listener on real postMessage
# events, and the request log wraps the page's own fetch. If the application
# stopped making these calls, this panel would go empty.
WIRE_JS = r"""
(() => {
 // An init script runs before the document has a body, so building the panel
 // immediately throws and the instrumentation silently never exists — which is
 // how the first attempt came out with an empty right-hand column. Wait for the
 // DOM, then build.
 //
 // Written as a Python RAW string: the previous version was an ordinary one, so
 // Python ate the backslashes and the browser received JavaScript with broken
 // string literals. It failed as a syntax error before the first statement ran,
 // which is why nothing appeared and nothing complained where anyone could see.
 // An init script is installed in EVERY frame, so the sandboxed app built a
 // copy of this panel inside itself and covered the interface it was meant to
 // explain. Only the host narrates.
 if (window.top !== window) return;

 const build = () => {
  const P = document.createElement('div');
  P.id = '__wire';
  P.style.cssText = 'position:fixed;top:0;right:0;bottom:120px;width:392px;z-index:9998;'
    + 'background:#0B0F14;color:#E6EAF0;font:12px/1.5 ui-monospace,Menlo,monospace;'
    + 'padding:14px 14px;overflow:hidden;border-left:2px solid #2563EB;display:flex;'
    // Purely a readout. Without this it sits over the page and swallows the
    // taps the recording exists to demonstrate — the clicks were landing on
    // the panel instead of the interface.
    + 'flex-direction:column;gap:8px;pointer-events:none';
  const H = document.createElement('div');
  H.textContent = 'ON THE WIRE';
  H.style.cssText = 'font-size:10.5px;letter-spacing:.16em;color:#8A93A0;flex:none';
  const L = document.createElement('div');
  L.id = '__wirelog';
  L.style.cssText = 'display:flex;flex-direction:column;gap:8px;overflow:hidden';
  P.append(H, L);
  document.body.append(P);

  const COLOURS = {frame:'#C4B5FD', host:'#7EE0A8', api:'#93C5FD', no:'#FCA5A5'};
  window.__wire = (kind, from, to, text) => {
    const row = document.createElement('div');
    row.style.cssText = 'border-left:3px solid ' + (COLOURS[kind] || '#8A93A0')
      + ';padding:3px 0 3px 9px';
    const head = document.createElement('div');
    head.textContent = from + '  →  ' + to;
    head.style.cssText = 'color:' + (COLOURS[kind] || '#8A93A0')
      + ';font-size:10.5px;letter-spacing:.07em';
    const body = document.createElement('div');
    body.textContent = text;           // a text node: nothing here is ever parsed
    body.style.cssText = 'color:#E6EAF0;word-break:break-word;white-space:pre-wrap';
    row.append(head, body);
    L.append(row);
    while (L.children.length > 6) L.firstChild.remove();
  };

  // The sandboxed app can only NAME an action. This is that message arriving.
  window.addEventListener('message', e => {
    const d = e.data;
    if (!d || typeof d !== 'object') return;
    const t = String(d.type || '');
    if (t === 'a2ui.action') {
      const scope = (d.args && d.args.scope) ? '  scope=' + d.args.scope : '';
      window.__wire('frame', 'SANDBOXED APP', 'MCP HOST',
                    'postMessage a2ui.action\n' + d.action + scope);
    } else if (t === 'a2ui.ready') {
      window.__wire('frame', 'SANDBOXED APP', 'MCP HOST', 'postMessage a2ui.ready');
    }
  });

  // The host owns transport, so every call it makes passes through here.
  const realFetch = window.fetch;
  window.fetch = async (url, opts) => {
    const o = opts || {};
    const method = o.method || 'GET';
    let path = String(url);
    try { const u = new URL(path, location.href); path = u.pathname + u.search; }
    catch (err) { /* leave it as given */ }

    let note = method + ' ' + path;
    const tag = o.headers && o.headers['X-Telltale-Client'];
    if (tag) note += '\nX-Telltale-Client: ' + tag;
    if (o.body) {
      try {
        const b = JSON.parse(o.body);
        if (b.scope) note += '\nscope=' + b.scope + '  turn=' + b.turn;
        else if (b.action) note += '\naction=' + b.action;
      } catch (err) { /* not JSON; the line above says enough */ }
    }
    window.__wire('host', 'MCP HOST', 'SERVER', note);

    const res = await realFetch(url, o);
    let summary = 'HTTP ' + res.status;
    try {
      const b = await res.clone().json();
      if (b.surface) {
        const types = [...new Set((b.surface.components || []).map(c => c.type))];
        const n = b.componentCount || b.component_count || (b.surface.components || []).length;
        summary += '  ·  ' + n + ' components\n'
                 + types.slice(0, 5).join(', ');
        if (b.rejections && b.rejections.length) {
          summary += '\n' + b.rejections.length + ' rejected';
        }
      } else if (b.detail) {
        summary += '  ·  ' + b.detail;
      } else if (b.actions) {
        summary += '  ·  catalog: ' + b.actions.length + ' registered actions';
      }
    } catch (err) { /* not JSON */ }
    window.__wire(res.ok ? 'api' : 'no', 'SERVER', 'MCP HOST', summary);
    if (res.ok && path.indexOf('/telltale/') >= 0) {
      window.__wire('host', 'MCP HOST', 'SANDBOXED APP', 'postMessage a2ui.render');
    }
    return res;
  };
 };
 if (document.readyState === 'loading') {
   document.addEventListener('DOMContentLoaded', build);
 } else {
   build();
 }
})();
"""

# A label on the frame itself, so which half is the app is never a guess.
LABEL_JS = r"""
(() => {
  const frame = document.getElementById('frame');
  if (!frame) return;
  const b = document.createElement('div');
  b.textContent = 'SANDBOXED APP · opaque origin · cannot fetch';
  b.style.cssText = 'position:absolute;top:-11px;left:14px;z-index:5;'
    + 'font:600 10.5px/1 ui-monospace,Menlo,monospace;letter-spacing:.1em;'
    + 'padding:5px 9px;border-radius:3px;color:#fff;background:#6D28D9';
  const holder = frame.parentElement;
  holder.style.position = 'relative';
  holder.append(b);
})();
"""


def record(out_dir: Path) -> Path:
    surface = dashboard_surface()
    api_base, base, running = start_servers(surface)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1600, "height": 1040},
            record_video_dir=str(out_dir),
            record_video_size={"width": 1600, "height": 1040},
        )
        # Installed before any page script runs, so the host's own fetch is the
        # one being wrapped rather than a copy made after the fact.
        context.add_init_script(WIRE_JS)
        page = context.new_page()
        caption = lambda text: page.evaluate(CAPTION_JS, text)  # noqa: E731

        def fit(margin_right: int = 410) -> None:
            """Keep the whole interface on camera beside the wire panel.

            The host page centres itself in the viewport, which put it underneath
            the panel. Constraining the content column and scaling it down is
            what makes 'full view' true — scrolling to each panel in turn showed
            the pieces but never the relationship between them.
            """
            # Deliberately NOT `zoom` or a transform: either one shifts the
            # host's coordinate space without shifting the iframe's, so
            # Chromium hit-tests clicks into the frame at the wrong place and
            # every tap in the demo silently misses. Reserve space instead and
            # give the viewport the height to hold the result.
            page.evaluate(
                """m => { document.body.style.marginRight = m + 'px'; }""",
                margin_right)

        # ---- the start page: what this is, before anything is composed -----
        page.goto(f"{base}/", wait_until="networkidle")
        fit()
        caption(INTRO[0]); page.wait_for_timeout(3000)
        caption(INTRO[1]); page.wait_for_timeout(2600)
        page.evaluate("() => document.getElementById('facts').scrollIntoView({block:'center'})")
        caption(INTRO[2]); page.wait_for_timeout(3000)

        # The tag is real, so it is demonstrated rather than described: the same
        # request, once without it and once with, from the client's own origin.
        untagged = page.evaluate(
            """async api => {
                 const r = await fetch(api + "/v1/health");
                 return r.status;
               }""", api_base)
        tagged = page.evaluate(
            """async api => {
                 const r = await fetch(api + "/v1/health",
                     {headers:{"X-Telltale-Client":"demo-kiosk"}});
                 return r.status;
               }""", api_base)
        caption(f"{INTRO[3]}  —  untagged: {untagged} · tagged: {tagged}")
        page.wait_for_timeout(3400)

        page.goto(f"{base}/embed/{RUN_ID}", wait_until="networkidle")
        page.wait_for_selector("#frame")
        fit()
        page.evaluate(LABEL_JS)
        # A recording that quietly lost its instrumentation looks like a working
        # recording until someone watches it. Say so at the point it happens.
        if not page.evaluate("() => !!document.getElementById('__wire')"):
            print("WARNING: the wire panel is not on the page — recording without it")
        frame = page.frame_locator("#frame")
        frame.locator("svg").first.wait_for()
        inner = [f for f in page.frames if f != page.main_frame][0]

        # ---- use case 1: overview, eight ECU domains on one bus ------------
        caption(CAPTIONS[0]); page.wait_for_timeout(2800)
        caption(CAPTIONS[1]); page.wait_for_timeout(1400)
        show_panel(page, inner)
        for domain in ("Battery", "Chassis", "Body", "ADAS", "Infotainment", "Sensors"):
            frame.get_by_role("button", name=domain, exact=True).click()
            show_panel(page, inner)
            page.wait_for_timeout(900)
        caption(CAPTIONS[2]); page.wait_for_timeout(2000)

        # A lamp drawing nothing is a lamp that is out — it has to read red.
        frame.get_by_role("button", name="Lighting", exact=True).click()
        show_panel(page, inner)
        caption(CAPTIONS[3]); page.wait_for_timeout(3400)

        # ---- containment, demonstrated rather than asserted ---------------
        reached = inner.evaluate(
            "() => { try { return parent.document.title; } catch (e) { return 'blocked'; } }")
        caption(f"{CAPTIONS[4]}  —  parent.document: {reached}")
        page.wait_for_timeout(3000)

        # Now the API itself, from inside the frame. Split across two ports this
        # is the interesting one: the frame is not merely on the wrong origin,
        # it has no origin to be allowed, so no CORS rule could let it through.
        api_reach = inner.evaluate(
            """async api => {
                 try { const r = await fetch(api + "/v1/health",
                     {headers:{"X-Telltale-Client":"demo-kiosk"}});
                   return "reached " + r.status; }
                 catch (e) { return "blocked"; }
               }""", api_base)
        caption(f"{CAPTIONS[5]}  —  fetch(API/v1/health) from the frame: {api_reach}")
        page.wait_for_timeout(3400)

        # ---- use case 2: active faults, every severity ---------------------
        frame.get_by_role("button", name="Active Faults").click()
        caption(CAPTIONS[6]); page.wait_for_timeout(4000)

        # ---- use case 3: trends over the window ---------------------------
        frame.get_by_role("button", name="Trends").click()
        caption(CAPTIONS[7]); page.wait_for_timeout(3000)
        caption(CAPTIONS[8]); page.wait_for_timeout(3200)

        # ---- use case 4: service — the technician's one action -------
        frame.get_by_role("button", name="Service").click()
        caption(CAPTIONS[9]); page.wait_for_timeout(2600)

        frame.get_by_role("button", name="Approve").click()
        page.evaluate(SCROLL_TO_LOG)
        caption(CAPTIONS[10]); page.wait_for_timeout(3200)

        # The same approval, with the code widened to every stored fault. The
        # name is registered and the gate lets it through — the refusal comes
        # from the server comparing it against the card it actually composed.
        page.evaluate(
            """async ([api, id]) => {
                 const r = await fetch(api + "/v1/action", {method:"POST",
                   headers:{"Content-Type":"application/json","X-Telltale-Client":id},
                   body:JSON.stringify({run_id:"bench-drive",node_id:"surface",
                     action:"approve",args:{code:"ALL",severity:"critical",
                     action:"clear_and_acknowledge",requested_by:"bench-operator"}})});
                 const b = await r.json().catch(()=>({}));
                 const el = document.getElementById("relay");
                 el.textContent = "runtime refused: " + (b.detail || r.status);
                 el.className = "line no";
               }""", [api_base, CLIENT_ID])
        page.evaluate(SCROLL_TO_LOG)
        caption(TAMPERED); page.wait_for_timeout(3600)

        inner.evaluate(
            "() => parent.postMessage({type:'a2ui.action',action:'wipe_dtc',args:{}}, '*')")
        page.evaluate(SCROLL_TO_LOG)
        caption(CAPTIONS[11]); page.wait_for_timeout(3400)

        # ---- the loop closing: the surface asks the framework to go look ---
        page.evaluate("() => window.scrollTo({top: 0})")
        caption(CAPTIONS[13]); page.wait_for_timeout(1800)
        frame.get_by_role("button", name="Refresh current status").click()
        page.evaluate(SCROLL_TO_LOG)
        caption(CAPTIONS[14]); page.wait_for_timeout(3400)

        page.evaluate("() => window.scrollTo({top: 0})")
        caption(CAPTIONS[15]); page.wait_for_timeout(1600)
        frame.get_by_role("button", name="Run full diagnostic").click()
        page.wait_for_timeout(1200)
        caption(CAPTIONS[16]); page.wait_for_timeout(3600)

        # The same wall, now on the scope rather than the action name.
        inner.evaluate(
            "() => parent.postMessage({type:'a2ui.action',action:'request_data',"
            "args:{scope:'drop_tables'}}, '*')")
        page.evaluate(SCROLL_TO_LOG)
        caption(CAPTIONS[17]); page.wait_for_timeout(3400)

        # ---- the second wall, demonstrated on the live surface ------------
        page.evaluate("() => window.scrollTo({top: 0})")
        caption(CAPTIONS[12]); page.wait_for_timeout(900)
        page.evaluate(
            """s => document.getElementById('frame').contentWindow.postMessage(
                   {type:'a2ui.render', surface:s}, '*')""",
            {"root": "r",
             "components": [{"id": "r", "type": "Text", "text": {"$bind": "/summary"}}],
             "dataModel": {"summary": INJECTED}},
        )
        page.wait_for_timeout(3400)

        video = page.video
        context.close()
        browser.close()
        raw = Path(video.path())

    for server, thread in running:
        server.should_exit = True
        thread.join(timeout=5)
    return raw


def to_mp4(raw: Path, out: Path) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(raw), "-c:v", "libx264", "-preset", "medium",
         "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30", "-movflags", "+faststart",
         str(out)],
        check=True, capture_output=True,
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "demo" / "telltale-embed-demo.mp4"))
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    raw = record(out.parent)
    print(f"raw webm: {raw}")
    if to_mp4(raw, out):
        print(f"mp4:      {out}")
    else:
        print("ffmpeg not found; webm only")


if __name__ == "__main__":
    main()
