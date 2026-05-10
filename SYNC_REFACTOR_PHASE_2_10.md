# Phase 2.10 — Klipper Motion Tracking Replaces G-code Shell Markers

> **Status:** PROPOSED. Companion to `SYNC_REFACTOR_PLAN.md`,
> `SYNC_REFACTOR_PHASE_2_8.md`, and `SYNC_REFACTOR_PHASE_2_9.md`.
> No new firmware features. All work is host-side: Python scripts,
> Klipper subscription client, and a sidecar G-code metadata file.
> Phase 2.9 calibration workflow (observe-only tuner, analyzer
> acceptance gate, review-only patch emission) is preserved end-to-
> end; only the *delivery channel* for marker events changes.

## 0. Decision Addendum

| # | Topic | Decision |
|---|---|---|
| K1 | No firmware change | Phase 2.10 lands entirely in `scripts/`. No `settings_t` change, no `SETTINGS_VERSION` bump. |
| K2 | Default channel after 2.10 lands | Klipper API motion tracking. Shell-marker path stays as documented fallback for installs where the API is unreachable. |
| K3 | Stdlib + pyserial only | No `requests`, `websockets`, `aiohttp`, `numpy`. Direct UDS via `socket` module. |
| K4 | Sidecar is offline-built | Slicer post-process produces a JSON sidecar next to the G-code. No live G-code parsing during the print. |
| K5 | Marker semantics preserved | Tuner still receives `NT:START`, `NT:LAYER:N`, `NOSF_TUNE:Feature:Vvfil` events. Internal API surface in `nosf_live_tuner.py` (`on_m118`) is reused without breaking changes. |
| K6 | Single-host scope | No Moonraker dependency. The Pi running Klipper hosts both the API socket and the tuner. |
| K7 | Subscription throughput | Target: ≤ 10 Hz status churn from `motion_report` is sufficient. Higher rates are firehose-prone and not required by the bucket Kalman filter. |
| K8 | Acceptance bar | Calibration G-code emits zero `RUN_SHELL_COMMAND` lines from `gcode_marker.py` in Klipper-API mode. Tuner observed bucket counters and LOCKED count match shell-marker run within ±10 % on the same input G-code. |

## 1. Summary

Today the calibration workflow injects two kinds of `RUN_SHELL_COMMAND`
lines into print G-code:

```gcode
RUN_SHELL_COMMAND CMD=nosf_marker PARAMS="NT:LAYER:0"
RUN_SHELL_COMMAND CMD=nosf_marker PARAMS="NT:Inner_wall:V1360"
```

Each line forks a Python process (`scripts/nosf_marker.py`), which
appends one line to `/tmp/nosf-markers-<id>.log`. Klipper's
`gcode_shell_command` is *blocking on the gcode queue* — the next
`G1` cannot start until the shell process exits. Even a 30 ms fork
overhead, multiplied by hundreds of feature changes and per-layer
markers, produces visible stutter and time-varying flow.

Phase 2.10 replaces this channel with:
- An **offline sidecar JSON** built once when the slicer post-process
  runs (`gcode_marker.py --emit sidecar`).
- A **stdlib UDS client** that subscribes to Klipper `motion_report`,
  `gcode_move`, `print_stats`, and `virtual_sdcard` updates.
- A **segment matcher** that maps the live `(file_position, Z, eventtime)`
  pairs to the sidecar and synthesises the same `NT:*` and
  `NOSF_TUNE:*` events the tuner already consumes.

End result: zero shell forks during print, zero G-code injection
beyond what the slicer already emits, and the Phase 2.9 tuner
internals stay untouched.

## 2. Background

### 2.1 Why `RUN_SHELL_COMMAND` markers stutter

`gcode_shell_command` is a Klipper extra that runs each command
**synchronously inside the gcode queue.** From the print thread's
perspective:

1. G-code parser reaches the `RUN_SHELL_COMMAND` line.
2. Klipper forks `python3 scripts/nosf_marker.py …`.
3. Python interpreter starts (~25 ms cold, less warm).
4. `nosf_marker.py` opens the log file in append mode, writes one
   line, closes, exits.
5. Klipper waits for `wait()` to return before scheduling the next
   `G1`.

Net per marker: 20–60 ms of gcode-queue stall. The tuner emits
markers on every layer change *and* every ≥ 5 % flow change. A
typical 4-minute calibration print produces 200–500 marker calls,
i.e. 4–30 seconds of accumulated gcode-queue stall. The toolhead
does not stop instantly — Klipper's lookahead drains its pipeline
first — but pressure-advance and extruder velocity both jitter
around the marker, which directly contaminates the `BP / RT / EST`
samples the tuner is using to learn buckets.

This is precisely the signal we are trying to measure cleanly. The
marker channel itself is degrading the measurement.

### 2.2 Why we still need feature/v_fil/layer events

The Phase 2.9 tuner buckets samples by
`bucket_label(feature, v_fil)`. The 25 mm³/s flow bin is computed
on the host, but the *feature label* originates in the slicer
(`; TYPE:Inner_wall`, `; FEATURE:Outer_wall`, …) and is only
visible while parsing the G-code. Once the slicer has produced
the G-code stream and Klipper plays it back, the feature label is
gone unless we tracked it ourselves.

Layer count similarly comes from `;LAYER:` / `;LAYER_CHANGE`
slicer comments and is needed for the `layers_seen` lock criterion
(Phase 2.9 §16).

Phase 2.10 must therefore preserve:
- **Feature label** per move (`Outer_wall`, `Inner_wall`,
  `Internal_solid_inf`, `Top_surface`, `Bridge`, …).
- **Volumetric flow `v_fil`** (mm³/s) per move, derived from
  `width × height × feedrate / fil_area`.
- **Layer index** per move.
- **Print start / end** markers.

## 3. Klipper API Findings

Sourced from <https://www.klipper3d.org/API_Server.html> and
<https://www.klipper3d.org/Status_Reference.html>; field names are
quoted verbatim where given.

### 3.1 Transport

- API server is enabled by launching `klippy.py` with
  `-a /tmp/klippy_uds`. The path is configurable per install. A
  systemd-managed Klipper typically already passes `-a` via the
  service file.
- Connection is a Unix domain socket (`AF_UNIX`, `SOCK_STREAM`).
- Authentication is **not documented** in the public docs. The
  socket inherits filesystem permissions of the directory it lives
  in. We must verify the tuner process runs as the same user as
  Klipper or has sufficient permissions on `/tmp/klippy_uds`. On
  most installs both run as `pi`.
- Rate limiting is **not documented**. Treat the channel as
  best-effort and rate-limit our own subscription frequency.

### 3.2 Wire format

- Each direction is a stream of JSON objects separated by ASCII
  `0x03` (ETX). No length prefix, no framing other than the
  delimiter byte.
- Request shape: `{"id": <int>, "method": "<verb>", "params": {…}}`.
- Response shape: server replies with the same `id` plus a
  `"result"` object, or asynchronous notifications keyed by the
  client-provided `response_template`.
- A reference Python framing helper exists at
  `klipper/scripts/whconsole.py` in the Klipper repo. We will not
  depend on it (Klipper repo not vendored), but its behaviour is
  the implementation reference.

### 3.3 Endpoints in scope

- `objects/list` — query the set of registered objects on the
  current `printer.cfg`. We use this once at startup to verify
  `motion_report`, `gcode_move`, `print_stats`, and
  `virtual_sdcard` are available.
- `objects/subscribe` — subscribe to a list of
  `{object_name: [field, …]}`. Klipper sends an initial snapshot
  followed by asynchronous deltas any time one of the requested
  fields changes. This is the workhorse endpoint.
- `gcode/subscribe_output` — explicitly discouraged for state
  parsing per the docs. We will not use it.

### 3.4 Field semantics actually used

| Object | Field | Notes |
|---|---|---|
| `motion_report` | `live_position` | `[X, Y, Z, E]` interpolated to current eventtime. E is cumulative absolute extruder position (Klipper's internal `E`, not slicer's). |
| `motion_report` | `live_velocity` | mm/s scalar of toolhead movement. |
| `motion_report` | `live_extruder_velocity` | mm/s scalar of E axis. Sign indicates retract vs extrude. |
| `gcode_move` | `position` | last commanded toolhead position **in slicer coordinate system**. Useful as discrete step ground truth. |
| `gcode_move` | `extrude_factor` | M221 multiplier. Required for `v_fil` correction. |
| `gcode_move` | `speed_factor` | M220 multiplier. Required for `v_fil` correction. |
| `print_stats` | `state` | `"printing" / "paused" / "complete" / …`. Drives START/FINISH synthesis. |
| `print_stats` | `filename` | absolute path of running gcode. Used to locate matching sidecar. |
| `print_stats` | `current_layer` / `total_layer` | only set if slicer emits `SET_PRINT_STATS_INFO`. We treat them as best-effort. |
| `virtual_sdcard` | `is_active` | `True` while printing from internal SD. |
| `virtual_sdcard` | `file_position` | byte offset into G-code being played. Primary handle for sidecar matching. |
| `virtual_sdcard` | `file_size` | total bytes; used for progress sanity checks. |
| `webhooks` | `state` | `"ready" / "shutdown" / "startup" / "error"`. Drives reconnect logic. |

Caveats verified from docs:
- `current_layer` is only populated if the slicer emits
  `SET_PRINT_STATS_INFO TOTAL_LAYER=… CURRENT_LAYER=…` macros.
  Many slicer profiles do not. Our matcher uses the sidecar's own
  layer table, not `print_stats.current_layer`.
- `live_position` / `live_velocity` are *interpolated*. They lag
  reality by Klipper's lookahead (typically < 50 ms). Acceptable
  for our 25 mm³/s flow binning.
- `gcode_move.position` is updated when a G1 is *parsed*, which
  is ahead of when it executes. We deliberately do not use this
  field for live matching — only for sanity checks.

### 3.5 What must be experimentally verified on Pi

These are the items the public docs do not nail down:

1. Default UDS path on the maintainer's install. Likely
   `/tmp/klippy_uds`. Confirm via `ps aux | grep klippy`.
2. Filesystem permissions on the UDS. Confirm the tuner can
   `connect()` running as the same user.
3. End-to-end subscription latency — wall-clock between
   `objects/subscribe` request and first delta. We assume < 200 ms;
   if higher, we may need a different bootstrap order.
4. Whether `virtual_sdcard.file_position` advances monotonically
   across `M73` updates, pause/resume, and object-cancellation.
   The docs do not say. Our matcher must tolerate non-monotonic
   advances by falling back to Z + cumulative E.
5. Behaviour during `RESPOND` and `M118` G-code: does
   `motion_report` emit a delta? Empirically expected to be no
   movement, no delta — but worth confirming so our START/FINISH
   detection does not also need a fallback.

## 4. Current NOSF Marker Flow

Phase 2.9 marker delivery, post-`f9c269c`:

```
slicer .gcode
   │
   ▼
scripts/gcode_marker.py
   ├── parses ;TYPE: / ;LAYER: / ;WIDTH / ;HEIGHT
   ├── computes v_fil = w·h·F / fil_area
   ├── injects:
   │     M118 NT:START
   │     RUN_SHELL_COMMAND CMD=nosf_marker PARAMS="NT:LAYER:N"
   │     RUN_SHELL_COMMAND CMD=nosf_marker PARAMS="NT:Inner_wall:V1360"
   │     ; original G1 lines preserved
   │     RUN_SHELL_COMMAND CMD=nosf_marker PARAMS="NT:FINISH"
   ▼
Klipper runs the G-code
   ├── M118 → echoed on serial (tuner reads via klippy log)
   └── RUN_SHELL_COMMAND → forks nosf_marker.py
                         → appends "<wall_ts> NT:Inner_wall:V1360" to
                           /tmp/nosf-markers-<id>.log
   ▼
scripts/nosf_live_tuner.py
   ├── tails marker file (--marker-file)
   ├── tails klippy.log (--klipper-log) for M118 echoes
   ├── feeds each line to on_m118(raw)
   └── on_m118 dispatches START / LAYER / FEATURE events
```

The tuner's internal API is already designed around `on_m118(raw_string)`,
so any new channel that ends in `tuner.on_m118("NT:Inner_wall:V1360")`
needs no other changes inside the Kalman / lock / persistence layers.

## 5. Core Constraint — Motion Does Not Encode Feature Type

The single hardest constraint of Phase 2.10:

**Klipper has no concept of slicer features.**

`motion_report` knows X/Y/Z/E and velocities. `gcode_move` knows
the parsed G-code position and override factors. `print_stats`
knows file name, state, and (sometimes) layer counters. None of
them know that the move at X=120, Y=80, Z=0.4, E=12.3 is part of
`;TYPE:Inner_wall`.

Three options exist for re-attaching that label:

| Option | Where the slicer info lives | Pros | Cons |
|---|---|---|---|
| Inject markers into G-code | inline in `.gcode` | already works | shell-fork stutter |
| Live-parse G-code in Python while Klipper plays it | re-read the file as it streams | no preprocess step | 8 KB/s file-position races, complex retract/relative-E handling, doesn't survive object cancellation |
| **Offline sidecar** | separate JSON next to `.gcode`, byte-position keyed | one-time cost, deterministic, survives pause/resume, stdlib | extra file, must ship with print |

We pick the offline sidecar (option 3). The slicer post-process
already parses every line of the G-code; we are just changing what
it emits.

## 6. Recommended Architecture

Pipeline diagram (target state after Phase 2.10):

```
┌────────────────────────────────────────────────────────────┐
│ slice  →  scripts/gcode_marker.py --emit sidecar           │
│            ├── writes input.nosf.json (sidecar)            │
│            └── writes input.gcode unchanged (default) OR   │
│                input.nosf.gcode with M118 NT:START/FINISH  │
│                only (no RUN_SHELL_COMMAND lines)            │
└──────────────────────────┬─────────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────────┐
│ Klipper plays input.gcode                                   │
│   - virtual_sdcard advances file_position                   │
│   - motion_report streams live_position/velocity            │
│   - print_stats transitions ready→printing→complete         │
└──────────────────────────┬─────────────────────────────────┘
                           │  Unix domain socket
                           │  /tmp/klippy_uds
                           ▼
┌────────────────────────────────────────────────────────────┐
│ scripts/nosf_live_tuner.py                                  │
│   ├── KlipperApiClient (stdlib socket + 0x03 framing)       │
│   │     - objects/list at startup                           │
│   │     - objects/subscribe to                              │
│   │       {motion_report, gcode_move, print_stats,          │
│   │        virtual_sdcard, webhooks}                        │
│   ├── SegmentMatcher                                        │
│   │     - holds the active sidecar                          │
│   │     - watches print_stats.filename → loads sidecar      │
│   │     - on each motion_report delta:                      │
│   │       picks segment by file_position, validates Z       │
│   │       and cumulative E, emits synthetic markers         │
│   └── tuner.on_m118(synthesised_raw)                        │
│         - START on print_stats state→"printing"             │
│         - LAYER:N on segment.layer change                   │
│         - NOSF_TUNE:<feature>:V<v_fil>:W<w>:H<h>            │
│           on segment.feature OR v_fil bin change            │
│         - FINISH on state→"complete"                        │
│   └── existing observe-only Kalman / persistence path       │
└────────────────────────────────────────────────────────────┘
```

**Primary architecture: A — direct Klipper UDS, stdlib socket.**

Rationale per K3 and K6:
- Single dependency (Python stdlib + pyserial).
- No HTTP server, no websocket library, no Moonraker.
- UDS is local-only, low latency, no auth surface.
- Works on a headless Pi without a Moonraker install.

**Option B — Moonraker WebSocket.** Considered and rejected as the
primary path because Moonraker is not guaranteed installed and
adds a websocket dependency. We will document Moonraker as a
**fallback** for users who already run it and find the direct UDS
unreachable (e.g. Klipper not started with `-a` and the user
cannot edit the service unit). The Moonraker JSON-RPC schema
mirrors the Klipper `objects/subscribe` schema closely; the same
matcher will run unchanged behind a thin transport adapter.

**Option C — current shell marker file flow.** Retained as
documented fallback for diagnostic and bring-up runs. The
`gcode_marker.py --emit file` path keeps working untouched.
Default emit mode flips to `sidecar` after Phase 2.10.5 lands.

## 7. Data Model — Sidecar Schema

Sidecar lives next to the G-code, named `<basename>.nosf.json`.
JSON, line-delimited not required, but each object should be
self-contained for streaming partial reads.

### 7.1 Top-level structure

```json
{
  "_schema": 1,
  "generator": "gcode_marker.py 2.10.0",
  "source_gcode": "input.gcode",
  "source_sha256": "abc123…",
  "filament_dia_mm": 1.75,
  "fil_area_mm2": 2.40528,
  "layers": [
    {"index": 0, "z_mm": 0.20, "byte_start": 1248, "byte_end": 12450},
    {"index": 1, "z_mm": 0.40, "byte_start": 12450, "byte_end": 27001}
  ],
  "segments": [
    {
      "byte_start": 1340,
      "byte_end": 1820,
      "layer": 0,
      "feature": "Outer_wall",
      "z_mm": 0.20,
      "width_mm": 0.45,
      "height_mm": 0.20,
      "feedrate_mm_per_min": 3000.0,
      "v_fil_mm3_per_s": 1660.5,
      "v_fil_bin": 1650,
      "e_start": 12.3,
      "e_end": 24.8,
      "x_start": 120.0, "y_start": 80.0,
      "x_end":   145.0, "y_end":   80.0
    }
  ]
}
```

### 7.2 Segment definition

A **segment** is a contiguous run of extruding G1 moves where
`(feature, layer, width, height, feedrate)` are stable enough that
their `v_fil_bin` does not change. The slicer's own grouping of
extrusion lines under a `;TYPE:` block is the natural unit; we
sub-divide a TYPE block whenever any of width/height/feedrate
changes within it.

`v_fil_bin` is the canonical 25 mm³/s bucket key already used by
the tuner: `int(round(v_fil / 25.0)) * 25`.

The segment list is dense — every extruding move belongs to
exactly one segment. Travel moves and pure retracts are excluded
(the tuner already filters them via the `est < MIN_LEARN_EST_SPS`
gate, but excluding them here makes the matcher cheaper).

### 7.3 Why byte positions

`virtual_sdcard.file_position` is the most reliable real-time
handle Klipper exposes for "which line are we on now?" Z and
cumulative E are derived state and can desync on retracts,
absolute/relative E switches, and pressure advance. Byte position
maps 1:1 to the G-code source as long as the file isn't rewritten
mid-print, and Klipper monotonically advances it for forward play.

`source_sha256` lets the matcher refuse to attach a sidecar to a
recompiled G-code with the same name; mismatched hash logs a
warning and falls back to "no feature labels" mode (still emits
START/FINISH/LAYER from sidecar layer table by Z if available).

### 7.4 Layer table

Separate from segments because layer transitions are events, not
durations. The matcher uses the layer table to decide when to
synthesise a `NT:LAYER:N` event.

## 8. Runtime Matching Algorithm

### 8.1 Inputs

Live state is updated by `objects/subscribe` notifications:

```
state := {
  file_position:   int,    # virtual_sdcard.file_position
  z_mm:            float,  # motion_report.live_position[2]
  e_mm:            float,  # motion_report.live_position[3]
  v_extrude:       float,  # motion_report.live_extruder_velocity
  speed_factor:    float,  # gcode_move.speed_factor (default 1.0)
  extrude_factor:  float,  # gcode_move.extrude_factor (default 1.0)
  print_state:     str,    # print_stats.state
  filename:        str,    # print_stats.filename
  printer_state:   str,    # webhooks.state
}
```

### 8.2 Segment lookup

Primary index: `file_position`.

```python
def find_segment(state, sidecar):
    fp = state.file_position
    # binary search by byte_start
    idx = bisect_right(sidecar.segment_starts, fp) - 1
    if idx < 0:
        return None
    seg = sidecar.segments[idx]
    if fp >= seg.byte_end:
        # between segments (travel, retract, comment)
        return None
    return seg
```

Validation gates before a segment is accepted as the active
context:

1. **Z guard.** `abs(state.z_mm - seg.z_mm) <= 0.5` mm. If Z
   diverges, fall back to "no feature" until file_position passes
   the next layer boundary.
2. **E direction guard.** If `state.v_extrude < 0` (retract), no
   feature event is synthesised. The tuner's existing
   `MIN_LEARN_EST_SPS` gate still guards the bucket update.
3. **Stale guard.** If file_position has not advanced for
   `STALE_FP_S` (default 2 s) and printer_state == "printing",
   treat as paused and emit no events. Prevents flicker on
   `M0` / `PAUSE`.

### 8.3 Event synthesis

At each subscription delta:

```
prev = matcher.last_segment
seg  = find_segment(state, sidecar)

# layer transitions
if seg and seg.layer != matcher.last_layer:
    tuner.on_m118(f"NT:LAYER:{seg.layer}")
    matcher.last_layer = seg.layer

# feature/v_fil transitions
if seg and (
        prev is None
        or seg.feature != prev.feature
        or seg.v_fil_bin != prev.v_fil_bin
        or seg.width_mm != prev.width_mm
        or seg.height_mm != prev.height_mm
):
    # apply M220/M221 corrections
    v_fil = seg.v_fil_mm3_per_s * state.speed_factor * state.extrude_factor
    tuner.on_m118(
        f"NOSF_TUNE:{seg.feature}:V{v_fil:.1f}"
        f":W{seg.width_mm:.2f}:H{seg.height_mm:.2f}"
    )

matcher.last_segment = seg
```

### 8.4 START / FINISH

Driven from `print_stats.state` transitions, not from G-code:

| Transition | Synthesised event |
|---|---|
| `→ "printing"` after sidecar load | `NT:START` |
| `→ "complete"` | `NOSF_TUNE:FINISH:0:0:0` |
| `→ "cancelled"` or `"error"` | `NOSF_TUNE:FINISH:0:0:0` (treat as terminal) |
| `→ "paused"` | no event; matcher freezes `last_segment` so resume continues cleanly |

### 8.5 Pathological cases handled

- **Object cancellation** (Klipper `EXCLUDE_OBJECT_START/END`): the
  matcher skips events while inside an excluded region — detected
  by `gcode_move.position` jumping backward within a single
  layer's `byte_start..byte_end` band. Conservative fallback is
  "no events" until file_position resumes monotonic advance.
- **Pressure advance** does not affect E direction sign, so the
  retract guard remains valid. PA does change instantaneous E
  velocity but the tuner already smooths via Kalman R.
- **Relative vs absolute E** (M82/M83): the sidecar stores absolute
  E spans only when the G-code is in M82. If M83 is detected
  during sidecar build, E spans are recorded as relative deltas
  and the matcher reconstructs cumulative E. This is documented
  in the sidecar header (`e_mode: "absolute" | "relative"`).
- **Arc moves** (G2/G3): expanded as straight segments by the
  sidecar builder; v_fil computed off the linear feedrate. Klipper
  itself converts arcs to straight segments at parse time, so file
  positions still align.
- **Pause / Resume**: handled by §8.4; matcher state survives the
  pause window because file_position resumes from where it left.
- **Unknown slicer**: sidecar builder prints a warning and emits
  segments with `feature: "Unknown"`. The tuner already accepts
  unknown features as a single bucket per `v_fil_bin`.

## 9. Integration Points

### 9.1 New scripts

#### `scripts/klipper_motion_tracker.py`

Either a standalone module imported by `nosf_live_tuner.py`, or a
new script if we want it usable independently for debugging. We
will import it. Public surface:

```python
class KlipperApiClient:
    def __init__(self, uds_path: str = "/tmp/klippy_uds"): ...
    def connect(self) -> None: ...                      # blocks; raises on failure
    def list_objects(self) -> list[str]: ...            # objects/list
    def subscribe(self, objects: dict[str, list[str]]) -> None: ...
    def poll(self, timeout_s: float = 0.05) -> Optional[dict]: ...
    def close(self) -> None: ...

class SegmentMatcher:
    def __init__(self, sidecar_path: str | None = None): ...
    def attach_sidecar(self, path: str) -> None: ...    # validates SHA256
    def update(self, state: dict) -> list[str]: ...     # returns list of synthetic raw m118 lines
```

The client exposes the JSON delta as a flat dict. Framing details
(0x03 splitter, partial-message buffer, async response routing)
are private.

#### `scripts/gcode_marker.py` — new mode

Adds `--emit sidecar` (and accepts the existing `m118 / mark / file
/ both` modes unchanged). When `--emit sidecar`:

- Sidecar JSON written next to the input G-code with `.nosf.json`
  suffix (or `--sidecar PATH` override).
- The output G-code is **identical to the input**, with two
  exceptions injected:
  - `M118 NT:START` near the top, between heaters-on and first
    travel move (operator can opt out with `--no-m118-start`).
  - `M118 NOSF_TUNE:FINISH:0:0:0` near the bottom.
  This pair is purely a backstop — even if the API client cannot
  attach to Klipper, the M118 echo in `klippy.log` lets the tuner
  still recognise print boundaries.
- No `RUN_SHELL_COMMAND` lines are emitted.

The existing `--emit m118 / mark / file / both` modes stay as the
fallback path. No regression risk for current users on day one.

### 9.2 Tuner additions

`scripts/nosf_live_tuner.py` gains:

- `--klipper-uds PATH` (default `/tmp/klippy_uds`).
- `--sidecar PATH` (override; otherwise derived from
  `print_stats.filename`).
- `--klipper-mode {auto, on, off}` (default `auto`):
  - `auto`: attempt UDS connect; on failure log a warning and fall
    back to existing marker-file / klipper-log tailing.
  - `on`: require UDS; exit non-zero if unreachable.
  - `off`: never attempt UDS; force shell-marker fallback (useful
    for bring-up).
- A new internal pump in `run_loop` that polls `KlipperApiClient`
  and feeds synthesised events through the existing `tuner.on_m118`.
- No changes to bucket Kalman, persistence, lock criteria, or
  patch emission.

### 9.3 Tuner internal API

`tuner.on_m118(raw)` is already the single ingress point for
START / LAYER / NOSF_TUNE / FINISH events. Phase 2.10 reuses it
verbatim. We do not introduce a parallel ingress; the synthesised
strings are byte-identical to what `gcode_marker.py` would have
produced via M118.

### 9.4 No changes to

- `nosf_analyze.py` (consumes JSON state and CSV; channel-agnostic).
- `nosf_marker.py` (kept for fallback path; otherwise unused).
- Any firmware file. AGENTS.md rule #1 build gate is N/A
  (no firmware change).

## 10. Milestones

```
[ ] 2.10.0  plan/doc (this file)                          docs only
[ ] 2.10.1  sidecar generator in gcode_marker.py          --emit sidecar
[ ] 2.10.2  KlipperApiClient prototype                    stdlib UDS
[ ] 2.10.3  SegmentMatcher + offline simulator            unit-tested
[ ] 2.10.4  tuner integration + auto-fallback             --klipper-mode
[ ] 2.10.5  analyzer/doc updates                          KLIPPER.md, MANUAL.md
[ ] 2.10.6  shell marker deprecation                      after ≥ 5 real prints
```

Each milestone is one commit + push per AGENTS.md rule #3.
No firmware build required for any 2.10 milestone.

### 2.10.0 — Plan / doc

This file. Commit the plan, push.

### 2.10.1 — Sidecar generator

**Files:**
- `scripts/gcode_marker.py` — add `build_sidecar(input_path, sidecar_path, dia)` and an `--emit sidecar` argparse path.
- `scripts/test_gcode_marker.py` — new file. Pure-stdlib pytest-free pattern matching `scripts/test_nosf_live_tuner.py`.

**Behaviour:**
- Walk the input G-code line by line. Track:
  - file byte position (use `infile.tell()` after each `readline()`).
  - active feature (`;TYPE:` / `;FEATURE:` / `; FEATURE:`).
  - active width / height (`;WIDTH:` / `;HEIGHT:` / `;layer_height=`).
  - active feedrate (last `F` parameter).
  - layer index (`;LAYER:N` and Orca `;LAYER_CHANGE`).
  - extruder mode (M82 / M83) and current absolute E.
- For each extruding G1, advance the current segment; close + start a new segment when feature/layer/width/height/v_fil_bin changes.
- Emit JSON per §7.

**Tests (offline, no Klipper):**
- `test_sidecar_orca_sample` — feed a stripped sample Orca file (committed under `tests/fixtures/orca_sample.gcode`), assert layer count, segment count > 0, every segment has `v_fil_bin > 0` and a known feature.
- `test_sidecar_relative_e` — M83 file; assert e_mode "relative" and reconstructed cumulative E within ±0.01 mm of expected.
- `test_sidecar_byte_positions_monotonic`.
- `test_sidecar_sha256_matches_source`.
- `test_sidecar_arc_expansion` — G2/G3 file; segments cover the arc length, v_fil derived from linear feedrate.

### 2.10.2 — KlipperApiClient prototype

**Files:**
- `scripts/klipper_motion_tracker.py` — `KlipperApiClient` class.
- `scripts/test_klipper_motion_tracker.py` — new file.

**Behaviour:**
- `connect(uds_path)`: opens `socket.socket(AF_UNIX, SOCK_STREAM)`, connects, sets non-blocking.
- Outgoing: serialise JSON, append `b"\x03"`, `sendall`.
- Incoming: ring buffer; split on `b"\x03"`, parse each chunk as JSON.
- `subscribe(objects)`: sends `objects/subscribe` with a fixed `response_template: {}` so async deltas use the request id.
- `poll(timeout_s)`: `select.select([sock], …, timeout_s)`; returns at most one parsed message.
- Reconnect: on `BrokenPipeError` / `ConnectionResetError`, raise; caller decides retry policy (the tuner already has a 5-attempt reconnect loop for the serial port; mirror that).

**Tests:**
- `test_framing_roundtrip` — fake UDS server (created via `socketpair`) echoes a known framed message; client parses.
- `test_partial_chunk` — server sends a JSON object split across two `recv` chunks; client reassembles.
- `test_two_messages_one_chunk` — server sends `{...}\x03{...}\x03` in one `recv`; client returns both.
- `test_garbage_disconnect` — server closes mid-message; client raises.

### 2.10.3 — SegmentMatcher + offline simulator

**Files:**
- `scripts/klipper_motion_tracker.py` — add `SegmentMatcher`.
- `scripts/test_klipper_motion_tracker.py` — extend.

**Behaviour:**
- Loads sidecar JSON; builds in-memory `segment_starts` array for `bisect`.
- `update(state)` consumes the dict from `KlipperApiClient.poll()` (or a synthetic dict in tests) and returns a list of raw `on_m118` strings.
- Implements §8 algorithm including Z guard, E retract guard, layer transitions, START/FINISH on print_stats edges.

**Tests:**
- `test_layer_transition_emits_event`.
- `test_feature_change_emits_nosf_tune`.
- `test_v_fil_bin_unchanged_no_event`.
- `test_retract_no_event`.
- `test_pause_resume_segment_state_survives`.
- `test_filename_change_loads_new_sidecar`.
- `test_compare_with_shell_marker_baseline` — feed the simulator a recorded delta stream that corresponds to a known calibration print; assert the synthesised event sequence matches (within ±10 % count) the events that the existing shell-marker pipeline emits on the same input G-code.

### 2.10.4 — Tuner integration

**Files:**
- `scripts/nosf_live_tuner.py` — wire `KlipperApiClient` and `SegmentMatcher` into `run_loop`. Add the three new flags from §9.2.
- `scripts/test_nosf_live_tuner.py` — extend with simulated Klipper deltas.

**Behaviour:**
- On startup, if `--klipper-mode != off`:
  - Try to connect to UDS; if `auto` and connect fails, log warning and continue without it.
  - If connect succeeds, list objects, subscribe to the four objects in §3.4.
- On each loop tick, `client.poll(0.05)` and feed any returned dict into `matcher.update(state)`. Each returned string passes through `tuner.on_m118`.
- The existing `--marker-file` / `--klipper-log` paths remain. If both Klipper-API and marker-file are wired, prefer Klipper-API events; the marker file is used only when no sidecar is loaded yet.

**Tests:**
- `test_auto_fallback_when_uds_missing`.
- `test_klipper_events_drive_buckets` — synthetic Klipper delta stream on a known sidecar produces the same bucket counters as a control run with marker-file input.
- `test_observe_only_no_set_writes_via_klipper_path`.

### 2.10.5 — Analyzer + doc updates

**Files:**
- `MANUAL.md` — new "Klipper API motion tracking" subsection. Document UDS path, sidecar build, fallback behaviour.
- `KLIPPER.md` — replace the marker-flow walkthrough with the sidecar + UDS flow. Keep the shell-marker workflow as "fallback / debug".
- `README.md` — update the calibration flowchart.
- `TASK.md` — append Phase 2.10 status.

No code change to analyzer; sidecar feeds the tuner, not the analyzer. Analyzer continues to read CSVs and JSON state.

### 2.10.6 — Shell marker deprecation

**Files:**
- `scripts/gcode_marker.py` — flip default `--emit` from current to `sidecar`. Print a one-line warning when `--emit file` (the marker-file mode) is used: "shell-marker mode is deprecated; prefer --emit sidecar".
- `scripts/nosf_marker.py` — print deprecation warning to stderr on invocation. Keep operational.
- `KLIPPER.md` — move the shell-marker section under a "Legacy" heading.

Trigger: ≥ 5 successful real-hardware calibration prints with the
new path on the maintainer's machine, no regression in tuner
LOCKED bucket count or analyzer acceptance gate.

## 11. Tests

Beyond the per-milestone tests above, three cross-cutting tests
must pass before 2.10.4 lands:

### 11.1 Offline replay parity

Stored as `scripts/test_phase_2_10_parity.py`. Inputs:
- `tests/fixtures/orca_sample.gcode`
- `tests/fixtures/orca_sample.markers.log` — captured shell-marker
  output from a known-good run on the same G-code.

Flow:
1. Build sidecar from the gcode.
2. Synthesise Klipper deltas from a recorded
   `motion_report` / `virtual_sdcard` trace (also under
   `tests/fixtures/`). For 2.10.3 we synthesise the trace by
   walking the sidecar's segment list at expected feedrates; for
   2.10.4 we capture a real trace from a Pi soak run.
3. Run `SegmentMatcher.update` over the trace.
4. Compare the produced event sequence to the marker log (ordered
   set of `(layer, feature, v_fil_bin)` tuples). Allow ±5 % count
   delta (different bin alignment near transitions is expected).

If parity fails, the matcher is wrong; do not ship 2.10.4.

### 11.2 Stutter audit

On the Pi, with a calibration G-code freshly built via
`--emit sidecar`, run a 4-minute print and confirm:

```bash
grep -c RUN_SHELL_COMMAND input.gcode      # expected: 0
journalctl -u klipper -f | grep nosf_marker # expected: silent
```

Also capture `klippy.log` and verify no `forking` lines for
`nosf_marker.py` during the print window.

### 11.3 Bucket-count regression

Same calibration G-code, run twice on the same hardware:
- Run A: shell-marker mode (current default).
- Run B: sidecar + UDS mode (new default).

After each run, dump `--state-info`. Compare:
- `runs_seen`, `layers_seen`, `cumulative_mid_s` for each bucket
  must agree within ±10 %.
- LOCKED bucket count after Path-A criteria evaluation must be
  equal or higher in B (we expect cleaner samples → faster lock).

Failure here means our matcher is dropping or duplicating events.

## 12. Documentation Updates

- `MANUAL.md` — new "Calibration: motion-tracker mode" subsection;
  update tuner flag list with `--klipper-uds`, `--klipper-mode`,
  `--sidecar`. Note that `--marker-file` and `--klipper-log`
  remain valid but are now fallbacks.
- `KLIPPER.md` — restructure §"Calibration Prints" so the primary
  path is sidecar + UDS. Keep an explicit "Fallback (shell-marker
  flow)" section. Add the UDS prerequisite (`klippy.py -a`) check
  command:
  ```bash
  ls -l /tmp/klippy_uds
  ps -ef | grep '[k]lippy.py'
  ```
- `README.md` — replace the shell-fork warning with the new flow.
- `CONTEXT.md` — note Phase 2.10 host-only delta in the Phase
  history block.
- `TASK.md` — record completed 2.10 milestones with short SHAs.

## 13. Risks / Open Questions

| ID | Risk / question | Why it matters | Mitigation / experiment |
|---|---|---|---|
| R-1 | UDS path differs per install | Connect fails on day one | `--klipper-uds` flag override; default scans `/tmp/klippy_uds` then `/tmp/klippy.sock`. Document `ps` check. |
| R-2 | UDS not enabled (no `-a`) | UDS missing entirely | `--klipper-mode auto` falls back to shell-marker path; KLIPPER.md explains how to add `-a` to the systemd unit. |
| R-3 | UDS permission denial | Tuner runs as different user | Document required user. If unworkable, document Moonraker fallback (Option B). |
| R-4 | `file_position` non-monotonic during cancellation | Matcher emits wrong segment | Z + cumulative-E sanity gate; on backwards jump, freeze events for `STALE_FP_S`. |
| R-5 | Slicer uses unfamiliar feature comment style | `feature: "Unknown"` | Sidecar builder logs every distinct comment seen; operator can add patterns to `FEATURE_RES`. |
| R-6 | Subscription delta volume too high at high motion rates | CPU/network backpressure | Throttle by dropping deltas where state has not crossed any segment / Z / file-position threshold; the matcher already does this implicitly. |
| R-7 | Sidecar SHA mismatch (operator re-slices without re-running marker) | Wrong events synthesised | Loud warning in tuner log; refuse to load mismatched sidecar. Operator must re-run `gcode_marker.py --emit sidecar`. |
| R-8 | Pressure-advance jitter in `live_extruder_velocity` | Borderline retract guard flips | Use `live_extruder_velocity` only for sign; the existing `MIN_LEARN_EST_SPS` filter handles magnitude noise. |
| R-9 | Object cancellation regions still emit feature events | Bias accumulates incorrectly | Detect `EXCLUDE_OBJECT_START/END` markers in the sidecar build and tag affected byte ranges as "skip"; matcher returns no event in skip ranges. |
| R-10 | Moonraker users want WebSocket path | Branch in the architecture | Defer; Phase 2.10 is UDS-only. Add Moonraker transport in 2.10.x if requested with hardware confirmation. |
| R-11 | Klipper restarts mid-print | Tuner loses subscription | `KlipperApiClient` handles `webhooks.state` transitions; on `shutdown`/`error` → reconnect when state returns to `ready`. |
| R-12 | Two clients subscribe to the same objects | Documented as fine by Klipper, but unverified at scale | Expect no issue (UDS allows multiple connections). Document anyway. |
| R-13 | `current_layer` in `print_stats` not populated | Unused; sidecar drives layer | None; using sidecar layer table is the documented fallback. |
| R-14 | M220 / M221 mid-segment changes | `v_fil` correction lags | Apply factors live (§8.3); accept 1-tick lag. |
| R-15 | Moonraker sidecar discovery is different | Filename path differs | Out of scope for 2.10; deferred. |

Open questions requiring Pi-side experiment, listed for the
implementer:

1. **Q-2.10-A** — actual UDS path on this install. Confirm via
   `cat /etc/systemd/system/klipper.service` or equivalent.
2. **Q-2.10-B** — does `objects/subscribe` deliver an initial
   snapshot, or only deltas after the first change? Docs imply
   snapshot-then-deltas. Confirm by subscribing while idle.
3. **Q-2.10-C** — does `motion_report` keep streaming during
   `M400` / `M105` heartbeats? Expected yes (continuous), but
   want to verify.
4. **Q-2.10-D** — does `virtual_sdcard.file_position` advance
   linearly with bytes, or by parsed-line count? Expected bytes;
   if line-count, sidecar byte indexing is invalid and we have
   to switch to line indexing. Easy to detect at startup by
   probing two known offsets.
5. **Q-2.10-E** — what does `print_stats.state` look like during
   `EXCLUDE_OBJECT`? Stays `"printing"` or transitions through
   `"paused"`? Determines whether §8.5 cancellation handling
   needs an extra branch.

## 14. Acceptance Gate

Phase 2.10 lands when ALL of:

1. `grep -c RUN_SHELL_COMMAND <calibration.gcode>` returns `0` for
   any G-code produced by `gcode_marker.py --emit sidecar`.
2. `nosf_live_tuner.py --klipper-mode on` connects to the local
   UDS, subscribes successfully, and runs through a complete
   print without disconnects.
3. Tuner internal counters after a calibration print:
   - `runs_seen`, `layers_seen`, `cumulative_mid_s` for the
     dominant-speed bucket of `Outer_wall` / `Inner_wall` /
     `Internal_solid_inf` are within ±10 % of a control run on
     the same G-code with the shell-marker path.
   - LOCKED bucket count ≥ control run's count.
4. Stutter audit shows no `nosf_marker.py` forks during the print
   window.
5. `nosf_analyze.py --acceptance-gate` accepts the calibration
   corpus produced via the new path (no new failure mode in the
   acceptance gate).
6. Phase 2.9 tests still pass:
   `python3 scripts/test_nosf_live_tuner.py`.
7. New tests pass:
   - `python3 scripts/test_gcode_marker.py`
   - `python3 scripts/test_klipper_motion_tracker.py`
   - `python3 scripts/test_phase_2_10_parity.py`

## 15. Rollback Plan

Rollback for each milestone is independently reversible because no
firmware change is touched.

| Failure | Action |
|---|---|
| 2.10.1 sidecar generator emits wrong segments | Operator continues using `--emit file`; revert `gcode_marker.py` commit. |
| 2.10.2 UDS client crashes / hangs | `--klipper-mode off` forces shell-marker fallback. Tuner functional, no calibration data lost. |
| 2.10.3 SegmentMatcher mis-attributes segments | Same: `--klipper-mode off`. Optionally revert the matcher commit. |
| 2.10.4 tuner integration regresses bucket counters | Pin tuner to last 2.9.x commit (post-`f9c269c`). New flags become no-ops. State file unchanged. |
| 2.10.5 docs incorrect | Doc-only revert. |
| 2.10.6 shell-marker users surprised by deprecation | Warning is non-fatal; the `--emit file` path still works. Defer 2.10.6 if pushback. |

State file impact: **none.** Phase 2.10 produces the same `on_m118`
strings the tuner already consumes; bucket Kalman state is
unaffected by which channel delivered the marker.

Sidecar files are operator-side artefacts. If the matcher is
wrong, operator deletes the sidecar; sidecar-less mode falls back
to "no feature labels", and the tuner still records a coarse
single-bucket-per-Vbin run that the analyzer correctly flags as
LOW confidence.

## 16. First Implementation Recommendation

**Do first:**
1. 2.10.0 (this plan) — landed.
2. 2.10.1 sidecar generator with a thorough offline test fixture
   set. The sidecar is the foundation; if it is wrong, nothing
   downstream works.
3. 2.10.2 stdlib UDS client with `socketpair` based unit tests
   that do not require a running Klipper. This proves the framing
   layer in isolation.

**Do not do first:**
- Do not start with the live tuner integration. Until §11.1 parity
  test passes against recorded fixtures, the integration only
  adds confounding variables.
- Do not chase Moonraker support. Single transport, single bug
  surface for v1.
- Do not deprecate the shell-marker path before the new path has
  flown ≥ 5 real prints. Phase 2.9 calibration is the maintainer's
  daily driver; keep the fallback hot.
- Do not introduce per-feature firmware behaviour. Phase 2.10 is
  about cleaner measurement, not about giving the firmware new
  inputs. (That door stays open under the deferred Phase 2.8
  Option B if data ever justifies it.)

---

**Cross-references:**

- `SYNC_REFACTOR_PLAN.md` — main plan, Phase 2.7 telemetry pipeline.
- `SYNC_REFACTOR_PHASE_2_8.md` — closed-loop tuner; superseded as
  default by 2.9; kept as debug path.
- `SYNC_REFACTOR_PHASE_2_9.md` — observe-only calibration workflow.
  Phase 2.10 preserves every guarantee in 2.9 §17 and §16; only
  the marker delivery channel changes.
- `scripts/gcode_marker.py` — sidecar generator added in 2.10.1.
- `scripts/nosf_live_tuner.py` — UDS pump added in 2.10.4.
- `scripts/klipper_motion_tracker.py` — new in 2.10.2 / 2.10.3.
- `KLIPPER.md` — reflows in 2.10.5.
- Klipper docs:
  <https://www.klipper3d.org/API_Server.html>,
  <https://www.klipper3d.org/Status_Reference.html>.
