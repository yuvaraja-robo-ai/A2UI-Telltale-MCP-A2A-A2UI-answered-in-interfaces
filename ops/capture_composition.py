"""Ask a model to compose an interface, and see what it reaches for.

This is the captured run behind the claim that a new catalog entry becomes part
of the vocabulary: the model is given the goal, the catalog and a data model, and
is never told which component to use. If it composes a GaugeCluster, it did so
because the catalog offered one and the data was shaped like its binding.

    ops/capture_component.sh
    OLLAMA_MODEL=gemma4:latest ops/capture_component.sh

The prompt is not written here. It is imported from ``s13code.runtime`` — the
same ``compose_system_prompt`` and ``compose_instruction`` the running framework
sends — because a capture that exercised a private copy of the prompt would
prove nothing about what ships. A test in S14Code asserts that neither of them
contains the word "GaugeCluster", so the claim this script rests on is enforced
by the repository being submitted rather than by this script's good intentions.

It lives outside S14Code because the fork is a general-purpose framework and
this is one machine's errand: a specific goal, a specific local model, a
specific endpoint. The pull request carries the RESULT — pasted into its
description — not the script that fetched it.

Writes the raw reply, the validated surface and the verdict to
``proofs/composition/`` at the project root. Exit status is 0 when the surface
validates clean, whatever the model chose: this reports what happened, it does
not insist.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx

from s13code.runtime import (compose_instruction, compose_system_prompt, merge_gauges,
                            repair_surface)
from s13code.ui.catalog import catalog_manifest
from s13code.ui.validator import validate_surface

# The project root, which holds both repositories and the captured evidence.
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "proofs" / "composition"

BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://192.168.32.2:11434").rstrip("/")
MODEL = os.getenv("OLLAMA_MODEL", "gemma4:latest")

# A goal whose answer genuinely contains bounded readings, a series and rows —
# three different shapes, so choosing the right component for each is a real
# choice and not the only move available. Nothing here is automotive, and
# nothing names a component.
GOAL = "show me the current state of the render farm before tonight's batch"

STRUCTURED = {
    "title": "Render farm — pre-batch check",
    "intro": "Four nodes online. One is close to its thermal limit and one has "
             "very little scratch space left.",
    "metrics": [
        {"label": "node-01 core temp", "value": 61, "min": 20, "max": 95,
         "unit": "degC", "warn": 78, "alarm": 88},
        {"label": "node-02 core temp", "value": 86, "min": 20, "max": 95,
         "unit": "degC", "warn": 78, "alarm": 88},
        {"label": "node-02 scratch free", "value": 42, "min": 0, "max": 2000,
         "unit": "GB", "warn": 300, "alarm": 100, "invert": True},
        {"label": "queue depth", "value": 118},
    ],
    "series": [
        {"label": "18:00", "value": 12}, {"label": "19:00", "value": 31},
        {"label": "20:00", "value": 64}, {"label": "21:00", "value": 118},
    ],
    "table": {
        "columns": ["Node", "State", "Jobs"],
        "rows": [
            {"Node": "node-01", "State": "idle", "Jobs": 0},
            {"Node": "node-02", "State": "rendering", "Jobs": 3},
            {"Node": "node-03", "State": "rendering", "Jobs": 2},
            {"Node": "node-04", "State": "draining", "Jobs": 1},
        ],
    },
}


def build_data_model() -> dict:
    """The generic data model, built the way ``compose_surface`` builds it."""
    model: dict = {
        "title": STRUCTURED["title"],
        "goal": GOAL,
        "summary": STRUCTURED["intro"],
        "intro": STRUCTURED["intro"],
        "metrics": STRUCTURED["metrics"],
        "series": STRUCTURED["series"],
        "series_values": [point["value"] for point in STRUCTURED["series"]],
        "spark": [point["value"] for point in STRUCTURED["series"]],
        "table_columns": STRUCTURED["table"]["columns"],
        "table_rows": STRUCTURED["table"]["rows"],
        "progress_value": 3,
        "progress_max": 4,
    }
    for index, metric in enumerate(STRUCTURED["metrics"]):
        model[f"metric_{index}_value"] = metric["value"]
    merge_gauges(model, STRUCTURED)
    return model


async def ask(system: str, instruction: dict) -> dict:
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": json.dumps(instruction)}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_ctx": 16384},
    }
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(f"{BASE_URL}/api/chat", json=payload)
    response.raise_for_status()
    body = response.json()
    return {"text": (body.get("message") or {}).get("content", ""),
            "model": body.get("model", MODEL)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    data_model = build_data_model()
    system = compose_system_prompt()
    instruction = compose_instruction(GOAL, catalog_manifest(), data_model)

    # The claim this capture rests on, checked rather than asserted.
    assert "GaugeCluster" not in system
    assert "GaugeCluster" not in json.dumps(instruction["compose"])
    print(f"asking {MODEL} at {BASE_URL} — the prompt never names a component")

    reply = asyncio.run(ask(system, instruction))
    raw = reply["text"]

    try:
        surface = json.loads(raw)
    except json.JSONDecodeError:
        surface = {}
    result = validate_surface(surface if isinstance(surface, dict) else {})
    types = [component.get("type") for component in result.accepted]
    # Exactly what compose_surface ships: validated, then made renderable.
    declared_root = surface.get("root") if isinstance(surface, dict) else None
    root_id, components = repair_surface(declared_root or "root", result.accepted)

    verdict = {
        "captured_at": datetime.now(UTC).isoformat(),
        "provider": "ollama",
        "model": reply["model"],
        "goal": GOAL,
        "prompt_named_the_component": False,
        "component_types_composed": types,
        "composed_gauge_cluster": "GaugeCluster" in types,
        "accepted": len(result.accepted),
        "rejected": [rejection.as_dict() for rejection in result.rejections],
        "clean": result.ok,
        "structure_reported": result.structure,
        "root_repaired": len(components) != len(result.accepted),
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "raw_reply.json").write_text(raw)
    (out / "surface.json").write_text(json.dumps(
        {"root": root_id, "components": components,
         "dataModel": data_model}, indent=2))
    (out / "verdict.json").write_text(json.dumps(verdict, indent=2))

    print(json.dumps(verdict, indent=2)[:1200])
    print(f"\nwrote {out}/verdict.json")


if __name__ == "__main__":
    main()
