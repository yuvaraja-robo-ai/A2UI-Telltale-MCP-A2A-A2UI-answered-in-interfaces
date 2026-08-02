# Splitting Part 1 from Part 2

**Date:** 2026-07-30
**Status:** approved design, not yet implemented

## Why

The S14 brief is two deliverables with two different homes:

- **Part 1** contributes a component to the shared catalog. It is a fork of
  `theschoolofai/S14Code`, a branch, and one pull request. Its audience is a
  reviewer reading a diff.
- **Part 2** is a UI-only application in a domain of my choosing. It is a
  hosted link. Its audience is somebody using it.

Today both live in one tree. `s13code/vehicle/` is an application sitting inside
a framework fork, and `s13code/ui/routes.py` imports it — which means the
framework cannot start without `cantools` installed, for reasons that have
nothing to do with the framework. That coupling is backwards and it puts
application code into a pull request that should carry a component.

This spec separates them.

## The boundary

Everything on the branch `pitlane-generative-ui` sorts into exactly one side.

### Part 1 — stays in `S14Code`, becomes the pull request

| File | Why it is Part 1 |
|---|---|
| `s13code/ui/catalog.py` | the `GaugeCluster` ComponentSpec — the contribution itself |
| `s13code/ui/client/app.html` | the renderer, in the viewer the brief names |
| `s13code/ui/client/index.html` | the renderer, in the showcase client |
| `s13code/runtime.py` | `gauges` in the generic data model (see *The blocker*) |
| `s13code/core/a2a_adapter/server.py` | a handler may answer with parts, not only text |
| `tests/test_gauge_cluster.py` | the spec's typed props and source tag |
| `tests/test_gauge_cluster_renderer.py` | the renderer draws from text nodes, executes nothing |
| `tests/test_client_rendering_playwright.py` | the render client in a real browser |
| `tests/test_invariants.py` | the three invariants, extended to name `GaugeCluster` |
| dev dep `playwright` | Part 1 has browser tests of its own |

### Part 2 — moves to `../telltale`, becomes the hosted app

| File | New home |
|---|---|
| `s13code/vehicle/*.py` (7 modules) | `telltale/vehicle/` |
| `s13code/vehicle/fixtures/bench_rig.dbc` | `telltale/vehicle/fixtures/` |
| `s13code/ui/client/embed_frame.html` | `telltale/web/` |
| `s13code/ui/client/embed_host.html` | `telltale/web/` |
| the `/embed/*` and `/v1/telltale/request` routes | `telltale/web/routes.py` |
| the A2A card wiring in `s13code/main.py` | `telltale/app.py` |
| `tests/test_vehicle_*.py` (4 files) | `tests/` |
| `tests/test_telltale_*.py` (2 files) | `tests/` |
| `tests/test_a2a_diag_*.py` (2 files) | `tests/` |
| `tests/test_embed_iframe_playwright.py` | `tests/` |
| `scripts/record_demo.py`, `demo/`, the Telltale design doc | same names |
| deps `cantools`, `python-can` | `telltale`'s own `pyproject.toml` |

`s13code/ui/routes.py` reverts to its upstream form. `S14Code` stops importing
`cantools` at module load.

## Layout

```
s14/
├── S14Code/                    Part 1 — the pull request
│   └── branch gaugecluster-catalog, off origin/main
│
└── telltale/                   Part 2 — the hosted app
    ├── pyproject.toml
    ├── telltale/
    │   ├── vehicle/            bus dbc profile socketcan telltale a2a_diag
    │   ├── web/                routes.py embed_frame.html embed_host.html
    │   └── app.py              FastAPI on 8115
    ├── tests/
    ├── scripts/
    └── docs/
```

## How Part 2 reaches Part 1

Three imports. No more.

```python
from s13code.ui.validator import validate_surface           # the wall
from s13code.ui.catalog import catalog_manifest, REGISTERED_ACTIONS
from s13code.core.a2a_adapter.server import A2ADemoServer
```

`telltale/pyproject.toml`:

```toml
[project]
dependencies = ["s14code", "cantools>=39", "python-can>=4.3", "fastapi", "uvicorn"]

[tool.uv.sources]
s14code = { path = "../S14Code", editable = true }
```

Telltale serves its own `/v1/catalog` directly from `catalog_manifest()`. The
catalog the browser gates actions against and the catalog the validator enforces
are then the same object in the same process — not two copies that can drift.

**One wall, two applications.** That is the claim the split has to preserve, and
this is how it does.

## The blocker in Part 1

`compose_surface` (`runtime.py:722-840`) builds a domain-neutral data model and
offers it to the model along with the catalog manifest. It exposes `results`,
`items`, `metrics`, `spark`, `table_rows`, `timeline`, `sections`, `series`,
`choices`.

The renderer's gauge contract (`app.html:167-174`) is:

```js
{label, value, min, max, unit, warn, alarm, invert}
```

`metrics` is `{label, value, unit}`. **There are no bounds anywhere in the data
model.** So Gemini cannot compose `GaugeCluster` into anything meaningful even
when it would like to: it would bind `/metrics`, the renderer would fall back to
a hardcoded 0–100, and the arc would be wrong. The catalog currently advertises
a component the data model cannot feed.

The brief's 20-point item is *"one captured run where Gemini composes your
component into a real interface without being told to name it, because
`compose_surface` reads the catalog and offers your component to the model on its
own."* That capture is not honest until the offer is real.

**Fix, in Part 1:** the content role's `metrics` entries gain optional `min`,
`max`, `warn`, `alarm`. Any metric carrying bounds is additionally projected to a
`gauges` pointer in the generic data model, beside `series` and `table`. Nothing
names a domain; a bounded measurement is not a vehicle idea.

Only then is the capture a fair test: the model is given bounded measurements
and picks the component for bounded measurements, with nobody naming it. If it
picks `StatTile` instead, that is the finding, and it goes in the verdict.

## Part 2 must carry three turns

The brief requires *"a conversation across at least three turns, where a tap in
one interface shapes the next"*. Telltale today has two server-composed scopes,
`status` and `diagnostics`; the domain tabs are client-side `Tabs`, which is one
surface, not a turn.

`POST /v1/telltale/request` therefore takes a scope with a parameter:

| Turn | Tap | Scope | Composes |
|---|---|---|---|
| 1 | open the app | `status` | header tiles, `GaugeCluster` per domain, DTC `Notice` |
| 2 | a domain | `domain:powertrain` | that ECU's gauges, `Sparkline` trend, signal `DataTable` |
| 3 | *Run diagnostics* | `diagnostics` | pass/flag/fail `DataTable`, stored-code `Timeline` |
| 4 | a failed check | `explain:P0217` | the gateway-composed turn (below) |

Each tap carries the previous turn's selection, so turn 3 scoped to a domain
reports that domain's checks first. That is the tap shaping the next interface,
not just navigating to it.

## Part 2 must reach the real gateway

Turns 1–3 compose deterministically from the bus. That satisfies *"never a
paragraph of text"* but not *"run end to end against the real gateway"*.

Turn 4 closes it. Tapping a failed check sends the diagnostic's structured
result to `S14Code`'s `POST /v1/agent/runs` with `respond_as: "ui"`, polls
`/v1/runs/{id}/composed`, validates the result, and renders it. The model
composes an interface explaining the fault. Because that payload is bounded
measurements, it is also the natural place to observe whether Gemini reaches for
`GaugeCluster` unprompted — the same capture the brief scores, taken from the
real application rather than a contrived prompt.

If the gateway is unreachable, turn 4 composes a `Notice` saying so. It never
falls back to prose.

## Attacking the boundary

Three prompts, three refusals, captured to `proofs/`:

1. a prompt engineered to emit a `RawHtml` node → unknown type rejected
2. a bound value whose content is `<img src=x onerror=...>` → refused as markup
3. an action name the catalog never registered → never crosses back

Each capture must show the **safe part of the interface still rendering**. A
blank page is not a demonstration; it is a different failure.

The same three run against `GaugeCluster` specifically, since the brief asks for
the invariants to hold *for the new component*.

## Testing

270 tests collect today. They divide **152 / 118**.

| Suite | Where | Count |
|---|---|---|
| `test_gauge_cluster.py` — the ComponentSpec | `S14Code` | 7 |
| `test_gauge_cluster_renderer.py` — the renderer | `S14Code` | 15 |
| `test_invariants.py` — the three invariants | `S14Code` | 8 |
| `test_client_rendering_playwright.py` — real browser | `S14Code` | 20 |
| upstream S13/S14 suites, untouched | `S14Code` | 102 |
| `test_vehicle_bus.py` | `telltale` | 14 |
| `test_vehicle_dbc.py` | `telltale` | 18 |
| `test_vehicle_profile.py` | `telltale` | 18 |
| `test_vehicle_socketcan.py` | `telltale` | 6 |
| `test_telltale_requests.py` | `telltale` | 15 |
| `test_telltale_route.py` | `telltale` | 7 |
| `test_a2a_diag_skills.py` | `telltale` | 12 |
| `test_a2a_diag_rpc.py` | `telltale` | 8 |
| `test_embed_iframe_playwright.py` — real browser | `telltale` | 20 |

Both suites must be green independently. 152 + 118 = 270: the split loses no
test, and the counts are the check that nothing was dropped in the move. `telltale`'s Playwright conftest
repoints at its own app; no test body changes.

SocketCAN tests skip when `vcan0` is absent and run when it is present. They
pin `receive_own_messages` in both directions, because the kernel does not echo
a frame to the sending socket and a test that assumes it does passes for the
wrong reason.

## Security rules, enforced not assumed

The brief: *"Secrets, .env, credentials, personal memory, and unrestricted paths
never enter the pull request. Synthetic identities are mandatory."*

`scripts/test.sh` in **both** repos ends with a scan that fails the run on:

- a tracked `.env` (only `.env.example`, placeholders only)
- an absolute path under `/home/`, `/Users/`, or `C:\`
- anything matching a Gemini/OpenAI/Anthropic key shape
- any real name, email, or VIN in fixtures — the bench rig uses a synthetic VIN
  and a synthetic operator

`S13_SANDBOX_ROOT` stays a placeholder in `.env.example` and is set from the
environment at runtime, never committed.

## Scripts

### `S14Code/scripts/serve.sh`

```bash
#!/usr/bin/env bash
# Part 1: the framework. Needs GLC on 8111 for any model-composed run.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env — copy .env.example and fill it"; exit 1; }
exec uv run uvicorn s13code.main:app --host 127.0.0.1 --port "${S13_PORT:-8113}"
```

### `S14Code/scripts/test.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check s13code tests
uv run pytest -q tests s13code/core
uv run playwright install --with-deps chromium >/dev/null 2>&1 || true
uv run pytest -q tests -k playwright
./scripts/check_no_secrets.sh
```

### `telltale/scripts/serve.sh`

```bash
#!/usr/bin/env bash
# Part 2: the application. Runs standalone; turn 4 needs S14Code on 8113.
set -euo pipefail
cd "$(dirname "$0")/.."
export S14_BASE_URL="${S14_BASE_URL:-http://127.0.0.1:8113}"
export S14_DBC_PATH="${S14_DBC_PATH:-$PWD/telltale/vehicle/fixtures/bench_rig.dbc}"
exec uv run uvicorn telltale.app:app --host 127.0.0.1 --port "${TELLTALE_PORT:-8115}"
```

### `telltale/scripts/vcan.sh`

```bash
#!/usr/bin/env bash
# Bring up a virtual CAN interface. Needs root; safe to re-run.
set -euo pipefail
ip link show vcan0 >/dev/null 2>&1 && { echo "vcan0 already up"; exit 0; }
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0
echo "vcan0 up — export S14_CAN_CHANNEL=vcan0 for a live bus"
```

### `telltale/scripts/test.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check telltale tests
uv run pytest -q tests                       # SocketCAN tests skip without vcan0
uv run playwright install --with-deps chromium >/dev/null 2>&1 || true
uv run pytest -q tests/test_embed_iframe_playwright.py
./scripts/check_no_secrets.sh
```

### `scripts/check_no_secrets.sh` — identical in both repos

```bash
#!/usr/bin/env bash
# The brief's rule, as a gate rather than a promise.
set -uo pipefail
fail=0
git ls-files | grep -qx '.env' && { echo "FAIL: .env is tracked"; fail=1; }
if git grep -nE '(/home/[a-z]|/Users/[A-Za-z]|C:\\\\Users)' -- \
     ':!*.lock' ':!docs/*' ':!*.md' | grep -v '/absolute/path/to/'; then
  echo "FAIL: an absolute personal path is committed"; fail=1
fi
if git grep -nE '(AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,})' -- .; then
  echo "FAIL: something key-shaped is committed"; fail=1
fi
exit $fail
```

## Documentation

### `S14Code`

- `docs/gaugecluster.md` — the ComponentSpec, the renderer's data contract, why
  the tone comes from a supplied threshold rather than fraction-of-range, and
  the three invariants demonstrated against the component.
- the pull-request description, carrying every item the brief lists: the
  component and one interface that used it, the application in a paragraph,
  three prompts and their three interfaces, the adversarial prompt and the
  refusal, an honest verdict, and the commands that reproduce it from a fresh
  checkout.

### `telltale`

- `README.md` — what it is, how to run it, the eight ECU domains, the ports.
- `docs/architecture.md` — block, sequence and flow diagrams, now showing the
  repo split and both entrances.
- `docs/a2a.md` — the skill tags, the three routing outcomes, curl examples.
- `docs/can.md` — the DBC, the signal parsers, the seven earned DTCs.
- `docs/security.md` — the opaque origin, why the host owns transport, and what
  the wall refuses.

## Every rule in `tasks`, and where it is met

### Part 1

| Rule | Where | State |
|---|---|---|
| a ComponentSpec in `s13code/ui/catalog.py` with typed props and a source tag | `catalog.py:96` — `GaugeCluster`, `title` text + `gauges` binding, `source="custom"` | done |
| a renderer in the render client that draws it from text nodes and executes no value | `app.html:130,167`; `index.html`. Every number is arithmetic on a data-model value; no `innerHTML`, no URL from a prop | done |
| one captured run where Gemini composes it **without being told to name it** | step 8, after the `gauges` data-model fix — the offer must be real first | **blocked on GLC** |
| an unknown type is rejected | `test_invariants.py`, extended to name `GaugeCluster` | to extend |
| a bound value carrying markup is refused | same, plus adversarial capture 2 | to extend |
| an unregistered action never crosses back | same, plus adversarial capture 3 | to extend |

### Part 2

| Rule | Where | State |
|---|---|---|
| every turn a composed, catalog-validated interface, **never raw text** | `/v1/telltale/request` composes and validates; a refusal returns no surface, never prose | done |
| the rich component the situation calls for | `GaugeCluster` for bounded signals, `Sparkline` for a trend, `DataTable` for checks, `Timeline` for stored codes, `Notice` for faults | done |
| a conversation across **at least three turns**, a tap shaping the next | the four-turn table above; turn 2 and 4 are new work | **to build** |
| runs end to end against the real gateway | turn 4 through `/v1/agent/runs` | **blocked on GLC** |
| an adversarial prompt, the validator refusing, the safe part still rendering | step 9, three captures | to build |
| fork, branch, **one** pull request | `gaugecluster-catalog` off `origin/main` | to open |
| a short screen recording of the app composing and reshaping live | `scripts/record_demo.py` re-run across the four turns | to record |
| a hosted app link | Part 2 deployed | to host |
| **no secrets, `.env`, credentials, personal memory, unrestricted paths** | `check_no_secrets.sh` gates both suites | to build |
| **synthetic identities are mandatory** | scanned 2026-07-30: `bench_rig.dbc` carries only signal definitions — no VIN, no operator, no name or email anywhere in the tracked tree. No personal path, no key-shaped string either | **verified, gate keeps it true** |

### The scoring trap, read literally

> *"An application that dumps one text block into a single component, however
> clever the prompt, scores nothing on variety."*

Telltale's risk is the opposite of the usual one — it is component-rich but
deterministic. Turn 4 is what stops it being a fixed screen with a CAN feed.

> *"An interface that renders model-authored markup fails the invariant it
> claims to hold."*

The frame runs at an opaque origin with `sandbox="allow-scripts"` and **no**
`allow-same-origin`. It cannot fetch, so the host owns transport. This is why
model-authored markup has no path to execution even if the validator were
bypassed — two independent barriers, not one.

## Order of work

1. `gauges` in the generic data model, with tests — Part 1's prerequisite
2. `gaugecluster-catalog` branch: Part 1 files only, suite green
3. `telltale` repo via `git filter-repo`, paths rewritten, history kept
4. `telltale/app.py`, `web/routes.py`, `pyproject.toml`; suite green
5. scripts and the secret gate in both repos
6. turn 2 and turn 4 — the parameterised scope and the gateway turn
7. GLC up, `.env` written and never committed
8. capture the unprompted `GaugeCluster` run
9. the three adversarial prompts and their refusals
10. documentation in both repos, then the PR description
11. record the demo, host Part 2

## What could go wrong

- **The gateway is not currently running** and there is no `.env`. GLC lives at
  `~/SchoolofAI/s13/glc_v3` and needs a Gemini key I do not have. Steps 7–8 are
  blocked on it. Everything before step 7 is not.
- **Gemini may not pick `GaugeCluster`.** That is a real experiment. If it picks
  `StatTile`, the verdict says so. Prompting it into compliance would forfeit the
  point the capture is meant to prove.
- **`git filter-repo` may not be installed.** Fallback is the fresh-repo option,
  losing Part 2's development record but nothing else.
- **The path dependency makes Part 2 harder to host.** If the host cannot take
  two trees, the fallback is publishing `s14code` as a wheel rather than
  vendoring a second copy of the wall.

---

*This spec is process documentation. It does not ship in the pull request.*
