"""Telltale's API server: data, composition, and the gate. No pixels.

  GET  /v1/health                   what this server is, and what it is reading
  GET  /v1/catalog                  the trusted component catalog
  GET  /v1/runs/{id}/composed       the interface composed for a run, re-validated
  POST /v1/telltale/request         a surface asking the framework to go and look
  POST /v1/action                   a validated user action (approve/reject/rerun)

The client is a separate process serving separate files (``telltale.web.client``)
and it holds none of this. That separation is the point: the renderer draws what
it is given and cannot reach a data source, the server owns every data source and
draws nothing. Neither half can be talked into doing the other's job.

Telltale is the application; S14Code is the framework it stands on. The catalog
served here, the catalog the validator enforces, and the catalog the browser
gates its actions against are the same in-process object imported from
``s13code.ui`` — one wall, two applications. Nothing in this module re-declares
a component type or an action name, because a second copy of a wall is a way for
the two to disagree.

Every call from a client carries an ``X-Telltale-Client`` tag naming which client
made it. It is not a credential and this module never treats it as one — an
untagged request is refused because it is malformed, not because the tag proves
anything. What it buys is attribution: the decision log can say which client
asked for what, and a request that arrives from something other than the shipped
client is visible rather than anonymous.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from s13code.ui.catalog import REGISTERED_ACTIONS, catalog_manifest
from s13code.ui.hitl import PendingAction, decide_resume
from s13code.ui.validator import validate_surface

from .. import agent
from ..vehicle import dashboard

router = APIRouter()

CLIENT_TAG_HEADER = "X-Telltale-Client"


def client_tag(x_telltale_client: str | None = Header(default=None)) -> str:
    """The name the calling client tags itself with, required on every request."""
    tag = (x_telltale_client or "").strip()
    if not tag:
        raise HTTPException(400, f"missing {CLIENT_TAG_HEADER} header")
    if len(tag) > 64 or not all(c.isalnum() or c in "-._:" for c in tag):
        raise HTTPException(400, f"malformed {CLIENT_TAG_HEADER} header")
    return tag


@router.get("/v1/health")
async def health(client: str = Depends(client_tag)):
    """What a client shows before it renders anything: is the server up, what is
    it reading, and how many components may cross the wall."""
    catalog, frames, source = dashboard.current_source()
    return {
        "service": "telltale-api",
        "source": source,
        "signals": len(catalog.signal_names()),
        "frames": len(frames),
        "scopes": list(dashboard.SCOPES),
        "actions": sorted(REGISTERED_ACTIONS),
        "components": len(catalog_manifest()["components"]),
        "client": client,
    }


@router.get("/v1/catalog")
async def catalog(client: str = Depends(client_tag)):
    return catalog_manifest()


class TelltaleRequest(BaseModel):
    action: str = "request_data"
    scope: str = "status"
    # What the client says about the tap that caused this: which turn of the
    # conversation it belongs to, and an id it can match the reply against. Both
    # are the client's own bookkeeping — the server echoes them and decides
    # nothing by them.
    turn: int = Field(default=0, ge=0)
    request_id: str = Field(default="", max_length=64)


@router.post("/v1/telltale/request")
async def telltale_request(body: TelltaleRequest, client: str = Depends(client_tag)):
    """Answer a request from a surface with a freshly composed surface.

    This is the loop closing: a tap in the interface asks the framework for the
    current status or for a diagnostic, the framework reads the bus, runs the
    checks and composes the reply. Nothing here trusts the caller — the page
    that sends this is as untrusted as anything else, so the action name is
    checked against the catalog, the scope against the recognised set, and the
    composed surface against the validator before any of it is returned.
    """
    if body.action not in REGISTERED_ACTIONS:
        raise HTTPException(409, f"unregistered action {body.action!r}")
    if body.scope not in dashboard.SCOPES:
        raise HTTPException(409, f"unknown scope {body.scope!r}")

    catalog, frames, source = dashboard.current_source()
    surface = dashboard.build_surface(catalog, frames, body.scope)
    result = validate_surface(surface)

    payload = {
        "scope": body.scope,
        "source": source,
        "requestedBy": client,
        "requestId": body.request_id,
        "turn": body.turn,
        "clean": result.ok,
        "componentCount": len(result.accepted),
        "rejections": [r.as_dict() for r in result.rejections],
        "surface": {"root": surface["root"], "components": result.accepted,
                    "dataModel": surface["dataModel"]},
    }
    if body.scope == "diagnostics":
        report = dashboard.run_diagnostics(catalog, frames)
        payload.update({"checksRun": report["checksRun"],
                        "checksFailed": report["checksFailed"],
                        "checksFlagged": report["checksFlagged"],
                        "storedCodes": report["storedCodes"]})
    return payload


class TurnBody(BaseModel):
    """One turn of the model-composed conversation."""

    kind: str = Field(default="overview", max_length=32)
    # What was tapped to get here. It lands inside a prompt template as data,
    # never as an instruction, and it is clamped because it originated in a
    # surface a model composed — the honest reading is that this is the one
    # place model output re-enters a prompt, and it is kept small and quoted
    # rather than pretended away.
    focus: str = Field(default="", max_length=120)
    turn: int = Field(default=0, ge=0)
    request_id: str = Field(default="", max_length=64)


@router.post("/v1/telltale/turn")
async def telltale_turn(body: TurnBody, client: str = Depends(client_tag)):
    """Ask the framework to compose this turn, and hand back what it composed.

    This is the path the assignment describes: a prompt to /v1/agent/runs with
    respond_as "ui", the composed interface read back and rendered. It runs
    against a separately running S14Code over HTTP, so a broken framework fails
    here loudly instead of silently falling back to the deterministic surfaces
    the rest of this application serves.
    """
    if body.kind not in agent.TURNS:
        raise HTTPException(409, f"unknown turn {body.kind!r}")
    try:
        composed = await agent.compose_turn(body.kind, body.focus)
    except agent.AgentError as error:
        raise HTTPException(502, str(error)) from error

    return {**composed, "requestedBy": client, "turn": body.turn,
            "requestId": body.request_id, "source": "s14code"}


class ActionBody(BaseModel):
    run_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    args: dict = Field(default_factory=dict)


def _resolve(prop, data_model: dict):
    """Read a ``{"$bind": "/pointer"}`` prop out of a data model."""
    if not isinstance(prop, dict) or "$bind" not in prop:
        return prop
    value = data_model
    for token in str(prop["$bind"]).lstrip("/").split("/"):
        if not isinstance(value, dict) or token not in value:
            return None
        value = value[token]
    return value


def _parked_approval(run_id: str, node_id: str) -> PendingAction | None:
    """The parameters the application actually offered for approval.

    The approval invariant is that an approval is bound to the *final*
    parameters, and that is only worth anything if the parameters come from the
    side that composed them. So the server rebuilds the surface it served and
    reads the ApprovalCard's own params out of it. A client that sends different
    args is disagreeing with what it was shown, which is exactly the case this
    check exists to catch — and it cannot escape by also declaring what it should
    be compared against, because it is not asked.
    """
    catalog, frames, _ = dashboard.current_source()
    surface = dashboard.build_surface(catalog, frames, "status")
    for component in surface["components"]:
        if component.get("type") != "ApprovalCard":
            continue
        model = surface["dataModel"]
        return PendingAction(run_id, node_id,
                             _resolve(component.get("summary"), model) or "",
                             _resolve(component.get("params"), model) or {})
    return None


@router.post("/v1/action")
async def action(body: ActionBody, client: str = Depends(client_tag)):
    pending = _parked_approval(body.run_id, body.node_id)
    if pending is None:
        raise HTTPException(409, "no action is waiting for approval")

    decision = decide_resume(pending, body.action, body.args)
    if not decision.allowed:
        # A tamper attempt is refused; the node stays waiting.
        raise HTTPException(409, decision.reason)
    return {"resumed": True, "node_id": body.node_id, "reason": decision.reason,
            "requestedBy": client}


@router.get("/v1/runs/{run_id}/composed")
async def composed(run_id: str, request: Request, client: str = Depends(client_tag)):
    """The interface the agent COMPOSED for this run, re-validated.

    Read in-process off ``request.app.state.s13_runtime``, the same attribute
    S14Code's own routes read — no HTTP hop between the application and the
    framework it embeds.
    """
    runtime = getattr(request.app.state, "s13_runtime", None)
    if runtime is None:
        raise HTTPException(503, "no runtime attached")
    try:
        snapshot = runtime.graph.snapshot(run_id)
    except KeyError:
        raise HTTPException(404, "run not found") from None

    res = (snapshot.nodes.get("surface") or {}).get("result") or {}
    surf = res.get("surface") or {}
    if not surf.get("components"):
        raise HTTPException(404, "run has no composed interface (no compose_surface node)")
    result = validate_surface(surf)
    return {
        "run_id": run_id,
        "finished": snapshot.finished,
        "surface": {"root": surf.get("root"), "components": result.accepted,
                    "dataModel": res.get("data_model") or surf.get("dataModel") or {}},
        "component_count": len(result.accepted),
        "clean": result.ok,
        "provider": res.get("provider"),
        "model": res.get("model"),
    }
