# EAG V3 · Session 14 — Generative UI

Two repositories and the operations that run them.

```
s14/
  S14Code/      Part 1 — the fork that becomes the pull request. Generic; no domain code.
  telltale/     Part 2 — the application. Vehicle diagnostics, answers only in interfaces.
  ops/          how to run, test and record any of it. Belongs to neither repo.
  demo/         the recorded end-to-end run
  proofs/       captured evidence produced by ops/
```

**Nothing in `ops/`, `demo/` or `proofs/` is inside either repository.** Starting
servers, recording a video and wiring a local model together are things *this
machine* does; they are not a contribution to the framework and not part of the
application. The one exception is deliberate and marked below.

---

## Run it

```bash
ops/up.sh          # gateway, framework, API, client — in dependency order
# open http://127.0.0.1:8121
ops/down.sh        # stop everything, by port
```

Ports, and who owns them:

| port | process | notes |
|---|---|---|
| 8112 | local model gateway | speaks GLC's contract, forwards to Ollama |
| 8113 | S14Code | the framework, HTTP |
| 8114 | S14Code | its A2A gRPC listener — **taken; do not reuse** |
| 8120 | Telltale API | the bus, composition, the gate. No HTML. |
| 8121 | Telltale client | the start page, the host, the frame. No data. |

The dashboard needs only the API and client. The gateway and framework are for
the turns a model composes.

Point it at a different model:

```bash
TELLTALE_OLLAMA=http://192.168.32.2:11434 \
TELLTALE_OLLAMA_MODEL=gemma4:latest ops/up.sh
```

## Test it

```bash
ops/test.sh                   # both suites
ops/test.sh framework         # S14Code only
ops/test.sh app -k gauge      # arguments after the repo name go to pytest
ops/check_no_secrets.sh       # the submission rule, as a gate, across both repos
```

The SocketCAN tests skip without a virtual bus:

```bash
sudo ip link add dev vcan0 type vcan && sudo ip link set up vcan0
```

## Capture the evidence

```bash
ops/record_demo.sh                        # -> demo/telltale-embed-demo.mp4 (starts its own servers)
ops/up.sh && ops/capture_turns.sh         # -> proofs/turns/        three composed turns
ops/up.sh && ops/capture_adversarial.sh   # -> proofs/adversarial/  the attacks, before and after
ops/capture_component.sh                  # -> proofs/composition/  composed without being named
```

None of it lands in either repository. What the pull request needs from these
runs is transcribed into `S14Code/README.md`, which is where the submission
checklist looks; the JSON here is the working copy behind that section.

`capture_adversarial.sh` has two halves. The regression half loads the validator
as it exists at `upstream/main`, executes it against a payload this branch
refuses, and records both verdicts — so "it used to get through" is a
measurement rather than a recollection. The live half sends hostile prompts to
the real framework and records whichever way they go, including the runs where
the model simply declined.

---

## What the two repositories are

**`S14Code/`** is the fork. It carries the catalog contribution — a `GaugeCluster`
component, its renderer, the structural checks and repairs that keep a composed
surface renderable, and the captured run. It has no vehicle code, no CAN
dependency, and no automotive vocabulary: the component draws a bounded
measurement against the limit its source defines, which is as true of a disk or
an SLO as of a coolant loop.

**`telltale/`** is the application. It reads a synthetic CAN bench rig and answers
every turn as a composed, catalog-validated interface. It depends on S14Code the
way any application depends on a library, imports the same validator and catalog
the pull request ships, and holds the parts that are nobody else's business — the
DBC, the bus reader, the local-model gateway shim.

Each has its own README with the detail.
