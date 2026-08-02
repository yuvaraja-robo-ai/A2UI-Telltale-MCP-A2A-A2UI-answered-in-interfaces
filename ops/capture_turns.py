"""Three turns, each shaped by the tap before it, through the real framework.

This is the evidence for the part of the brief that asks for a conversation
carried across at least three turns where a tap in one interface shapes the
next, running end to end against the gateway.

    ops/up.sh                 # gateway, framework, api, client
    ops/capture_turns.py      # -> proofs/turns/

Each turn is a real HTTP round trip: Telltale posts a prompt to S14Code's
/v1/agent/runs with respond_as "ui" and reads the composed interface back. What
lands in proofs/turns/ is what the model actually composed, including the
attempts that failed — a turn that needed three tries is a different claim from
one that worked first time, and the record should not be able to hide it.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "proofs" / "turns"

#: The conversation. Each turn's focus is what was tapped in the turn before,
#: which is the whole point: turn three is a question only turn two could ask.
CONVERSATION = [
    ("overview", ""),
    ("domain", "Lighting"),
    ("fault", "C0040"),
]


async def main() -> int:
    from telltale.agent import AgentError, compose_turn

    OUT.mkdir(parents=True, exist_ok=True)
    summary = []
    failed = 0

    for index, (kind, focus) in enumerate(CONVERSATION, start=1):
        try:
            turn = await compose_turn(kind, focus)
        except AgentError as error:
            # Reported, not swallowed. A turn the framework could not compose is
            # a finding about the model, and pretending otherwise would make the
            # rest of this record worthless.
            print(f"turn {index} ({kind}): FAILED — {error}")
            summary.append({"turn": index, "kind": kind, "focus": focus, "error": str(error)})
            failed += 1
            continue

        (OUT / f"turn{index}_{kind}.json").write_text(json.dumps(turn, indent=2))
        row = {
            "turn": index, "kind": kind, "focus": focus,
            "componentTypes": turn["componentTypes"],
            "componentCount": turn["componentCount"],
            "clean": turn["clean"], "coherent": turn["coherent"],
            "rejections": len(turn["rejections"]),
            "structureProblems": len(turn["structure"]),
            "model": turn["model"], "attempts": turn.get("attempts", []),
        }
        summary.append(row)
        print(json.dumps({k: row[k] for k in
                          ("turn", "kind", "focus", "componentTypes", "componentCount",
                           "clean", "coherent")}))

    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {OUT}/summary.json")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
