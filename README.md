# Telltale

A vehicle diagnostics application that answers **only by composing an interface**.
No turn returns a paragraph of text. A tap is not a link — it is a request that
earns the next interface.

This is **Part 2** of the S14 assignment. It is an application, not a framework:
the component catalog, the validator and the render invariants live in
[S14Code](../S14Code), which Telltale depends on the way any application depends
on a library. Part 1 is the pull request against that repository, and it contains
no automotive code at all.

---

## Run it

Two processes, because they are two different jobs.

```bash
uv sync

# the API: data, composition, the gate — serves no HTML
TELLTALE_API_PORT=8120 uv run telltale-server

# the UI: the start page, the host, the frame — holds no data
TELLTALE_API=http://127.0.0.1:8120 TELLTALE_CLIENT_PORT=8121 uv run telltale-client
```

Then open **<http://127.0.0.1:8121>**. The first screen explains what the
application is, probes the server, prints what it is actually reading, and offers
the two ways in.

One process instead of two, when one port is enough:

```bash
uv run telltale
```

Tests, including the browser-level ones:

```bash
uv run pytest -q
```

The SocketCAN tests skip unless a virtual bus exists:

```bash
sudo ip link add dev vcan0 type vcan && sudo ip link set up vcan0
```

### Or from the project root

Starting servers, recording the demo and pointing the framework at a local model
are operations, not application code, so they live one level up in `ops/` and are
in neither repository:

```bash
ops/up.sh              # this app, plus S14Code and a local model gateway
ops/test.sh app        # this suite
ops/record_demo.sh     # the recorded end-to-end run
```

### Configuration

| Variable | Default | What it does |
|---|---|---|
| `TELLTALE_API_PORT` | `8114` | port the API server binds (the project root uses 8120 — S14Code's A2A gRPC listener takes 8114) |
| `TELLTALE_CLIENT_PORT` | `8115` | port the client server binds (8121 from the project root) |
| `TELLTALE_API` | *(same origin)* | where the client sends its requests |
| `TELLTALE_CLIENT_ID` | `telltale-web` | what this client tags its requests with |
| `TELLTALE_CLIENT_ORIGINS` | `127.0.0.1:8115`, `localhost:8115` | which origins the API answers |

---

## Three tiers, each narrower than the last

```
   API server  :8114     every data source — DBC, bus, composition, validator
        │                answers only the named client origins
        │  HTTP, tagged with X-Telltale-Client
   client      :8115     three HTML files. No bus, no DBC, no validator.
        │
        │  postMessage — the only door left
   frame                 pixels. sandbox="allow-scripts", NO allow-same-origin,
                         so its origin is opaque: it cannot read the host page,
                         its storage, or the API.
```

Withholding `allow-same-origin` is the whole security posture. The cost is that
the frame cannot fetch either, which is *why* the surface arrives by message: the
host owns transport because the frame is incapable of it.

Splitting client from server is the same idea one tier out. The client is the
half an attacker reaches first, and after the split there is nothing behind it —
`tests/test_client_server.py` asserts that the client module imports no bus, no
DBC and no validator, so the separation cannot quietly erode.

---

## How a tap becomes the next interface

1. The frame **names an action**. Naming is all it can do — it has no transport.
2. The host checks that name against the catalog the server served. An action
   nobody registered never becomes a request.
3. The host tags the request and sends it:

   ```http
   POST /v1/telltale/request
   X-Telltale-Client: telltale-web

   {"action": "request_data", "scope": "diagnostics",
    "turn": 3, "request_id": "telltale-web-3-m1x4z"}
   ```

4. The server reads the bus, composes a fresh surface, **validates it**, and
   replies with the surface plus `requestedBy`, `turn` and `requestId`.
5. The host posts the surface into the frame — and drops any reply whose `turn`
   is older than the newest tap, so a slow answer cannot overwrite a newer screen.

### What the client tag is, and what it is not

`X-Telltale-Client` is **attribution, not authentication**, and the server never
treats it as a credential — it proves nothing, and anyone can send one. What it
buys is that the server's log can name which client asked for what instead of
recording "someone". A request with no tag is refused as malformed; a tag that is
not a plain name is refused before it can reach a log line. The rule has no
exception carved out for the easy endpoint, `/v1/health` included, because an
exception is how a rule stops being checkable.

---

## Every turn is a composed interface

| Turn | What the person does | What comes back |
|---|---|---|
| 1 | opens `/embed/live` | eight ECU domain panels of gauges, stored faults, trends, one service action |
| 2 | taps a domain | that domain's signals, drawn on the scales the DBC defines |
| 3 | taps **Refresh current status** | the bus is re-read and a new surface is composed — not a re-render of the old one |
| 4 | taps **Run full diagnostic** | 21 checks, each reporting the value it read and the limit it broke |
| 5 | taps **Approve** on a service card | the action is gated against the catalog and reaches the runtime |

Component variety is driven by the shape of the data, not by prompt wording:
`GaugeCluster` for bounded measurements, `DataTable` for stored codes,
`Sparkline` for a series over the window, `Timeline` for a sequence,
`ApprovalCard` for a decision, `StatTile` for a single number.

---

## Attacking the wall this stands on

Three things are refused, and the safe part of the interface still renders:

- **an unknown component type** — the validator drops it and keeps the rest,
- **a bound value carrying markup** — it renders as literal text; the client
  builds text nodes and assigns no `innerHTML` anywhere,
- **an unregistered action** — the host refuses the name and it never becomes a
  request.

The demo recording sends all three against the running application rather than
asserting them in prose, and `ops/capture_adversarial.sh` (one level up, in
neither repository) sends them again as prompts through the model that composes
the turns — recording whichever way they go.

On the last run, `gemma4` declined all three: it composed an interface *about*
the refusal instead of the payload, and swapped a registered action in for the
invented one. That is written down as what it is — a fact about one model on one
day, not a property of the wall. The wall is checked separately, by handing it
the surface a complying model would have produced.

---

## Layout

```
telltale/
  telltale/
    app.py              the three ways to run it: API, client, or both
    web/routes.py       the API — data, composition, the gate
    web/client.py       the client — three HTML files, no data
    web/client/         index.html (start here), embed_host.html, embed_frame.html
    vehicle/            DBC, bus, SocketCAN, drive profile, dashboard composition
  tests/                the suite, browser-level tests included
```

The bench rig is synthetic: `telltale/vehicle/fixtures/bench_rig.dbc` describes a
made-up vehicle, carries no VIN and no identity, and `ops/check_no_secrets.sh`
from the project root fails the build if that ever stops being true.
