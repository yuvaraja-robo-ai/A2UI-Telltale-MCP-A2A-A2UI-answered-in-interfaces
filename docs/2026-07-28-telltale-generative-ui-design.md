# Telltale — whole-vehicle health and diagnostics, answered only in UI

> In ISO 2575 a *telltale* is the dashboard indicator lamp: the one element of a vehicle whose
> entire job is to tell the truth about it. That is the property this application is built around —
> a generated interface that cannot show green through a stored fault, or red without one.

Design document. Session 14 assignment (Generative UI, A2UI, AG-UI) built on `S14Code`.

Date: 2026-07-28

## 1. Goal

An application in the vehicle-diagnostics domain where the agent answers **only** by composing a
catalog-validated interface, never with a paragraph of text. The data it presents is real telemetry
read off a CAN bus by edge hardware, not numbers the model invented.

Two deliverables, one pull request:

- **Part 1** — a new component, `GaugeCluster`, contributed to the shared catalog.
- **Part 2** — Telltale, a multi-turn UI-only application built on top of it.

## 2. Why telemetry is the right domain for this assignment

The rubric pays 15 points for *genuine variety: the right rich components for the data*. CAN
telemetry produces, from one bus, every shape the catalog cares about:

| Data shape on the bus | Component the situation calls for |
| --- | --- |
| Bounded instantaneous signals (RPM, coolant, state of charge) | `GaugeCluster` |
| One signal over a time window | `Sparkline` |
| Diagnostic trouble codes with attributes | `DataTable` |
| When each fault fired during a drive | `Timeline` |
| A destructive action (clear a stored code) | `ApprovalCard` |
| A bus with nothing wrong on it | `Notice` |

The variety is a property of the data, not of prompt engineering. That is the point.

It also sharpens the session's honesty rule. A generated interface can lie in a way a paragraph
cannot, by drawing an authoritative chart over data that does not exist. A gauge showing an invented
coolant temperature is exactly that failure. So the numbers must come from hardware.

## 3. Architecture

### 3.1 Data path

Everything reads SocketCAN. That is the whole portability argument: the virtual bus and the physical
bus present the same interface, so moving from bench to hardware changes one environment variable and
nothing else.

**Phase 1 — virtual bus, no hardware** (the development and grading path):

```
replayer (python-can) ──▶ vcan0 ──▶ CAN reader ──▶ S14Code :8113 ──▶ glc_v3 :8111
  DBC-encoded frames        SocketCAN   decode via DBC      graph + surface     gateway
  scripted drive profile                5 min ring buffer
```

**Phase 2 — real hardware** (the recorded demo):

```
ESP32-C3 + SN65HVD230 ──CAN 500 kbit──▶ Jetson can0 ──▶ (identical from here)
```

The Jetson carries a native CAN controller (`mttcan`), so the ESP32 wires to it directly through a
transceiver. No bridge machine sits in the middle. The reader binds to the interface named by
`S14_CAN_CHANNEL`, default `vcan0`; the demo sets `can0`.

Bench simulation, not a live vehicle. The scripted profile is idle → acceleration → thermal climb →
`P0217` engine-overtemperature set, so the recorded demo is repeatable and no car is required.

Bringing up the virtual bus:

```bash
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan && sudo ip link set up vcan0
```

Host already carries `can-utils`, `python-can` 4.6.1 and `cantools`.

### 3.2 The DBC is the source of truth for scale

The DBC is loaded **at runtime** from the path in `S14_DBC_PATH` and parsed with `cantools`. It is
never committed to the repository and never embedded in a prompt.

A DBC signal definition already carries exactly the fields a gauge needs:

```
signal EngCoolantTemp  →  {label: "Coolant", value: 96, min: -40, max: 215, unit: "degC"}
```

So gauge bounds come from the vehicle database, not from the model and not from a hardcoded table.
The model cannot choose a flattering scale that makes an overheating engine look nominal, because it
never sees the axis — it only chooses which component to use. This is the honesty rule from the
session applied one level deeper than the component tree.

Until the real DBC arrives, a small bench DBC describing this rig's own messages ships in
`s13code/ui/fixtures/bench_rig.dbc`. It is a stand-in for development, replaced by pointing
`S14_DBC_PATH` at the real file. No behaviour depends on which one is loaded.

If no DBC is available for a signal, the reader emits the raw value with no bounds, and the surface
gets a `Notice` rather than a gauge with invented limits. An unscaled number is not a gauge.

### 3.3 Trust boundary for the telemetry read

The Pi/HTTP hop is gone, so the tool boundary moves to MCP. A small MCP server owns the bus and
exposes three tools:

| Tool | Effect |
| --- | --- |
| `read_signals(window_s)` | decoded signals over a time window, with DBC bounds |
| `list_dtcs()` | stored diagnostic trouble codes |
| `clear_dtc(code)` | destructive; routed through the graph's waiting node and an `ApprovalCard` |

S14Code is the MCP client. The model never supplies a channel name, a message ID or a raw frame — it
reasons over what the tool returned. Same authority pattern `web_search` already follows: the tool
registry holds the authority.

Replay for a grader with no hardware and no bus: with `S14_CAN_CHANNEL` unset the reader replays a
bundled recording, `s13code/ui/fixtures/can_replay.jsonl`. Every interface in the submission
reproduces from a fresh checkout; the recorded demo still shows a real bus.

### 3.4 Where the work lands in the graph

Additive changes to the existing runtime, following the shapes already there:

1. `_work_intent()` (`s13code/runtime.py`) gains a `compose_telemetry` mode, selected when
   `respond_as == "ui"` **and** the prompt matches a vehicle-intent pattern — a word from a small
   fixed set (`car`, `vehicle`, `engine`, `bus`, `telemetry`, `fault`, `dtc`, `rpm`, `coolant`),
   matched case-insensitively on word boundaries. Its first frontier is a single
   `TaskSpec("telemetry", "can_telemetry", ...)`. The check runs before the existing
   `compose_answer` branch so a vehicle prompt reaches hardware instead of the model.
2. The deterministic planner extends the same pattern `compose_answer` uses: `telemetry` succeeds →
   add a `diagnose` node (LLM, data-only, interprets frames into the domain-neutral structured
   schema) → `diagnose` succeeds → add the terminal `surface` node (`compose_surface`).
3. `compose_surface` builds the data model from real node outcomes and asks Gemini to compose only
   the component tree. Every displayed value stays in the harness-owned data model behind a
   `{"$bind": "/pointer"}`. `validate_surface` gates the result before it becomes the node's output.

### 3.5 The one known friction point

`compose_surface` currently folds a node's `structured` result into the data model only for
`content` / `distill` / `answer` nodes. The `telemetry` and `diagnose` nodes need the same
treatment, so gauges and charts can bind to real numeric fields.

Change: pass through any succeeded node's `structured` dict into the data model under its node key,
instead of special-casing three node names. This keeps the builder domain-agnostic — it does not
learn the word "telemetry" — and is the first thing to verify during implementation, because every
later turn depends on it.

## 4. Part 1 — the `GaugeCluster` component

### 4.1 Catalog entry

In `s13code/ui/catalog.py`:

```python
"GaugeCluster": ComponentSpec("GaugeCluster", {
    "title":  PropSpec("text"),
    "gauges": PropSpec("binding"),   # [{label, value, min, max, unit, tone}]
}, source="custom"),
```

Two properties, both inert. `title` is literal text the client never treats as markup. `gauges` is a
JSON Pointer into the data model. There is no property a client would ever execute, so the catalog
cannot be used to smuggle a handler.

### 4.2 Renderer

A renderer in both clients (`s13code/ui/client/app.html` and `index.html`), built the way `spark()`
and `bars()` are already built: SVG elements via `document.createElementNS`, labels via
`document.createTextNode`. No `innerHTML` assignment, no value evaluated, no URL followed. Each gauge
draws a background arc and a value arc, with the sweep derived from `(value - min) / (max - min)`
clamped to `[0, 1]`.

### 4.3 Why the agent will reach for it unprompted

`compose_surface` reads the catalog and offers every component to the model. A data model carrying
`{label, value, min, max, unit}` objects makes `GaugeCluster` the obvious fit, so the model selects
it without the prompt naming it. That unprompted selection is the evidence Part 1 is scored on, and
it is captured as a recorded run.

### 4.4 Usefulness beyond this application

Any bounded metric against a threshold — a budget against a cap, a quiz score against a passing mark,
a battery against its floor. The component becomes part of the vocabulary the whole batch composes
with, which is the stated purpose of Part 1.

## 5. Part 2 — the Telltale application

Every turn is a composed, catalog-validated interface. A tap in one interface shapes the next.

| Turn | Trigger | Interface composed |
| --- | --- | --- |
| 1 | "How is my car doing right now?" | `GaugeCluster` (RPM, coolant, state of charge) + a `StatTile` row + `Sparkline` of the last 60 s of RPM + `Button`s offering the next moves |
| 2 | tap **Faults** | `DataTable` of stored codes (code, ECU, severity, first seen) + `Timeline` of when each fired. A clean bus yields a `Notice`, not an empty table |
| 3 | tap a code | `Card` explaining the code + `GaugeCluster` of the related signals at fault time + a `Button` offering to clear it |
| 4 | tap **Clear this code** | `ApprovalCard` bound to the exact clear parameters; approval resumes the parked node through `POST /v1/action` |

The shell is the existing `/app` viewer pattern: start a run with `respond_as: "ui"`, read
`/v1/runs/{id}/composed`, render, turn a tap into the next turn.

Turn 4 exercises the human-in-the-loop path. Clearing a stored diagnostic code is a genuinely
destructive action — it discards freeze-frame data a mechanic may still need — so it is the honest
place for an approval gate rather than a decorative one. `decide_resume()` compares the approved
arguments against the parked node's parameters, so a tampered `approve` is refused and the node
stays waiting.

## 6. The three invariants under attack

Before submission, an adversarial prompt attacks the wall the application stands on. One prompt
asking for an HTML dashboard containing an `<img onerror=...>` status badge and a `wipe_dtc` button
should produce refusals across all three invariants:

| Attack in the prompt | Invariant | Expected refusal |
| --- | --- | --- |
| `RawHtml` node | Catalog | unknown component type |
| `onclick` on a `GaugeCluster` | Data-not-code | unknown property |
| Markup bound into a `text` property | Data-not-code | text property carries markup |
| A `wipe_dtc` action | Event | unregistered action |

The safe part of the interface must still render while the hostile parts are rejected.
`POST /v1/validate` already returns exactly this rejection list, so the refusal is captured as
recorded output rather than a claim.

`REGISTERED_ACTIONS` stays closed. Telltale does not need a new action name; taps feed the next turn
as prompts, and the approval path uses the existing `approve` / `reject`.

## 7. Tests

Extending `tests/test_invariants.py` and `tests/test_s14_ui.py`:

- a surface using `GaugeCluster` with bound gauges validates clean
- `GaugeCluster` carrying an `onclick` property is rejected as an unknown property
- a gauge label bound to a value containing markup is rejected
- a surface emitting `wipe_dtc` is rejected as an unregistered action
- the CAN reader in replay mode returns byte-identical output across two calls
- a signal present in the DBC yields a gauge carrying that signal's own `min`, `max` and `unit`
- a signal absent from the DBC yields a `Notice`, never a gauge with invented bounds
- the composed surface for a replayed telemetry run contains at least one bound numeric series and
  no component outside the catalog

The existing suite (roughly 110 tests across the runtime, memory, A2A and UI layers) must stay green.

## 8. What goes in the pull request

1. The `GaugeCluster` component and one interface that used it.
2. Telltale in one paragraph: the domain, and what each turn composes.
3. Three prompts and the three different interfaces they produced.
4. The adversarial prompt and the validator refusing it.
5. An honest verdict on where composing the interface at runtime beat a fixed screen and where it
   fell short.
6. The commands that reproduce everything from a fresh checkout.

No secrets, no `.env`, no credentials, no personal memory, no unrestricted paths. Synthetic
identities only — the vehicle is a bench rig, the user is `student-01`.

## 9. Evidence to collect while building, for the honest verdict

The verdict is scored on engaging real trade-offs, so the measurements are gathered during
implementation rather than recalled afterwards:

- per-turn composition latency against the gateway, compared with a fixed screen
- whether Gemini selected `GaugeCluster` unprompted, and how often
- layout drift: does turn 3 rearrange controls the user learned in turn 1
- wrong-component cases, for example a `BarChart` where a `Sparkline` was the right form
- what the interface did when telemetry was missing — an honest `Notice`, or an invented gauge

A finding that generative UI is not ready for this job is an acceptable conclusion, provided the
evidence is there.

## 10. Protocol coverage, and what is deferred

The session's stack has five boundaries. Where each one sits in this build:

| Boundary | Where it lands | Phase |
| --- | --- | --- |
| **MCP** — agent ↔ tools | the CAN MCP server: `read_signals`, `list_dtcs`, `clear_dtc` | main build |
| **Graph** | `telemetry → diagnose → surface`, journal, waiting node for `clear_dtc` | main build |
| **A2UI** — what to render | composed component tree, `$bind` pointers, closed catalog | main build |
| **AG-UI** — agent ↔ surface | `/v1/runs/{id}/events` SSE, `STATE_DELTA` repaint, reconnect via `STATE_SNAPSHOT` | main build |
| **A2A** — agent ↔ agent | a vehicle agent that returns its **own** A2UI payload, validated against this client's catalog before rendering | fifth turn, after turns 1–4 are green |
| **MCP Apps** — UI inside a host | the Telltale surface as a `ui://` resource in a chat host | only if the A2A turn lands early |

The A2A turn is the one worth reaching for: it hits a benchmark row from the session verbatim, and it
is the only place where "the catalog is client-controlled" stops being a claim and becomes a
demonstration, because the surface genuinely arrived from another machine. `s13code/core/a2a_adapter/`
already exists, so it is wiring rather than invention.

It is nonetheless sequenced after the recording. None of MCP, A2A or MCP Apps is scored directly;
65 of the 100 points are the component, the multi-turn UI-only application, and a demo that runs. A
submission with a complete protocol stack and a demo that did not run scores zero by the assignment's
own words.

Out of scope entirely:

- Live vehicle data. Bench simulation only.
- Accessibility work on the new component beyond what the existing catalog widgets already do.
- Any new registered action name.
