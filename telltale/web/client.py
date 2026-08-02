"""Telltale's client: pixels only. It holds no data and reads no bus.

  GET  /                 what this is and how to use it, before anything renders
  GET  /embed/frame      the renderer, sandboxed, with no transport of its own
  GET  /embed/{scope}    the host that embeds the renderer at an opaque origin

Run this as its own process, on its own port, pointed at an API server:

    TELLTALE_API=http://127.0.0.1:8114 uv run telltale-client

Three tiers, each one narrower than the last. The API server owns every data
source. This client owns transport to it and nothing else. The frame inside this
client owns pixels and cannot reach either — it is embedded with
``sandbox="allow-scripts"`` and deliberately WITHOUT ``allow-same-origin``, which
makes its origin opaque, so it can neither read this page nor call the API. The
surface reaches it by ``postMessage`` because that is the only door left.

Every call this client makes to the API is tagged with its client id in the
``X-Telltale-Client`` header, so the server's log can say who asked.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter()

FILES = Path(__file__).parent / "client"

#: Where the API server lives. Empty means "same origin as this page", which is
#: what the combined single-process app uses.
API_BASE = os.getenv("TELLTALE_API", "").rstrip("/")

#: What this client tags its requests with. Configurable so two clients pointed
#: at one server are distinguishable in its log.
CLIENT_ID = os.getenv("TELLTALE_CLIENT_ID", "telltale-web")


def _page(name: str, **subs: str) -> str:
    path = FILES / name
    if not path.exists():
        raise HTTPException(500, f"client file missing: {name}")
    text = path.read_text()
    for key, value in {"__API_BASE__": API_BASE, "__CLIENT_ID__": CLIENT_ID, **subs}.items():
        text = text.replace(key, value)
    return text


@router.get("/", response_class=HTMLResponse)
async def index():
    """The first screen: what Telltale is, whether the server is up, and the two
    ways in. Shown before any surface renders, because a person meeting this for
    the first time needs to know what they are looking at."""
    return _page("index.html")


@router.get("/embed/frame", response_class=HTMLResponse)
async def embed_frame():
    """The ``ui://`` resource itself. Declared before /embed/{scope} so the
    literal path wins the match."""
    return _page("embed_frame.html")


@router.get("/embed/{scope}", response_class=HTMLResponse)
async def embed_host(scope: str):
    """An MCP Apps-style host: it embeds the surface in a sandboxed frame, feeds
    it the composed surface by message, and gates every action the frame names
    against the catalog the server hands back."""
    return _page("embed_host.html", __RUN_ID__=scope)
