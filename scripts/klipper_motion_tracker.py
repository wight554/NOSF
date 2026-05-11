#!/usr/bin/env python3
"""Klipper API motion tracker helpers for NOSF Phase 2.10.

Pure stdlib. The Klipper API server speaks JSON-RPC-ish objects over a
Unix domain socket, with each JSON object terminated by ASCII ETX (0x03).
"""

import json
import os
import select
import socket
import sys
import time
from bisect import bisect_right
from collections import deque
from typing import Dict, List, Optional


ETX = b"\x03"
DEFAULT_UDS_PATH = "/tmp/klippy_uds"
Z_GUARD_MM = 0.5
STALE_FP_S = 2.0
SUBSCRIBE_OBJECTS = {
    "motion_report": ["live_position", "live_velocity", "live_extruder_velocity"],
    "gcode_move": ["speed_factor", "extrude_factor", "position"],
    "print_stats": ["state", "filename", "current_layer"],
    "virtual_sdcard": ["is_active", "file_position", "file_size"],
    "webhooks": ["state"],
}


def klipper_status_payload(message: dict) -> dict:
    """Return the Klipper payload that may contain a status delta."""
    result = message.get("result")
    if isinstance(result, dict) and "status" in result:
        return result
    params = message.get("params")
    if isinstance(params, dict) and "status" in params:
        return params
    return {}


def merge_status_update(cache: dict, message: dict) -> Optional[dict]:
    payload = klipper_status_payload(message)
    status = payload.get("status")
    if not isinstance(status, dict):
        return None
    for obj, fields in status.items():
        if isinstance(fields, dict):
            cache.setdefault(obj, {}).update(fields)
    if "eventtime" in payload:
        cache["_eventtime"] = payload.get("eventtime")
    return cache


def matcher_state_from_status(cache: dict) -> dict:
    motion = cache.get("motion_report", {})
    gcode = cache.get("gcode_move", {})
    print_stats = cache.get("print_stats", {})
    virtual_sdcard = cache.get("virtual_sdcard", {})
    webhooks = cache.get("webhooks", {})
    position = motion.get("live_position") or gcode.get("position") or []
    z_mm = position[2] if len(position) >= 3 else None
    e_mm = position[3] if len(position) >= 4 else None
    return {
        "eventtime": cache.get("_eventtime"),
        "file_position": virtual_sdcard.get("file_position"),
        "file_size": virtual_sdcard.get("file_size"),
        "is_active": virtual_sdcard.get("is_active"),
        "filename": print_stats.get("filename"),
        "print_state": print_stats.get("state"),
        "current_layer": print_stats.get("current_layer"),
        "printer_state": webhooks.get("state"),
        "z_mm": z_mm,
        "e_mm": e_mm,
        "v_extrude": motion.get("live_extruder_velocity", 0.0),
        "live_velocity": motion.get("live_velocity"),
        "speed_factor": gcode.get("speed_factor", 1.0),
        "extrude_factor": gcode.get("extrude_factor", 1.0),
    }


class KlipperApiClient:
    def __init__(self, uds_path: str = DEFAULT_UDS_PATH):
        self.uds_path = uds_path
        self.sock = None
        self._next_id = 1
        self._rx = b""
        self._messages = deque()

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.uds_path)
            sock.setblocking(False)
        except Exception:
            sock.close()
            raise
        self.sock = sock
        self._rx = b""
        self._messages.clear()

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _require_sock(self):
        if self.sock is None:
            raise ConnectionError("Klipper API socket is not connected")
        return self.sock

    def _request_id(self) -> int:
        req_id = self._next_id
        self._next_id += 1
        return req_id

    def _send(self, obj: dict) -> None:
        sock = self._require_sock()
        payload = json.dumps(obj, separators=(",", ":")).encode("utf-8") + ETX
        sock.sendall(payload)

    def _send_request(self, method: str, params: Optional[dict] = None) -> int:
        req_id = self._request_id()
        msg = {"id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        return req_id

    def list_objects(self) -> List[str]:
        req_id = self._send_request("objects/list")
        while True:
            msg = self.poll(1.0)
            if msg is None:
                raise TimeoutError("timeout waiting for objects/list response")
            if msg.get("id") != req_id:
                self._messages.append(msg)
                continue
            result = msg.get("result", {})
            objects = result.get("objects", [])
            if isinstance(objects, dict):
                return sorted(objects)
            return list(objects)

    def subscribe(self, objects: Dict[str, List[str]]) -> int:
        return self._send_request(
            "objects/subscribe",
            {"objects": objects, "response_template": {}},
        )

    def poll(self, timeout_s: float = 0.05) -> Optional[dict]:
        if self._messages:
            return self._messages.popleft()

        sock = self._require_sock()
        readable, _w, _x = select.select([sock], [], [], timeout_s)
        if not readable:
            return None

        try:
            chunk = sock.recv(4096)
        except (BrokenPipeError, ConnectionResetError):
            self.close()
            raise
        if not chunk:
            self.close()
            raise ConnectionResetError("Klipper API socket closed")

        self._rx += chunk
        while ETX in self._rx:
            raw, self._rx = self._rx.split(ETX, 1)
            if not raw:
                continue
            self._messages.append(json.loads(raw.decode("utf-8")))

        if self._messages:
            return self._messages.popleft()
        return None


def _sha256(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class SegmentMatcher:
    def __init__(self, sidecar_path: Optional[str] = None):
        self.sidecar_path = None
        self.sidecar = None
        self.segments = []
        self.segment_starts = []
        self.last_segment = None
        self.last_layer = None
        self.last_print_state = None
        self.started = False
        self.finished = False
        self.last_filename = ""
        self.last_fp = None
        self.last_fp_t = 0.0
        if sidecar_path:
            self.attach_sidecar(sidecar_path)

    @property
    def attached(self) -> bool:
        return self.sidecar is not None

    def attach_sidecar(self, path: str) -> None:
        with open(path) as fh:
            data = json.load(fh)
        sha = data.get("source_sha256")
        if sha:
            source_path = os.path.join(os.path.dirname(os.path.abspath(path)), data.get("source_gcode", ""))
            if os.path.exists(source_path):
                actual = _sha256(source_path)
                if actual != sha:
                    msg = f"sidecar SHA mismatch for {source_path}: expected {sha}, got {actual}"
                    print(f"[motion-tracker] {msg}", file=sys.stderr)
                    raise ValueError(msg)
            else:
                print(
                    f"[motion-tracker] warning: source gcode not found for SHA check: {source_path}",
                    file=sys.stderr,
                )
        else:
            print("[motion-tracker] warning: sidecar has no source_sha256; skipping SHA check", file=sys.stderr)

        self.sidecar_path = path
        self.sidecar = data
        self.segments = list(data.get("segments", []))
        self.segment_starts = [int(seg["byte_start"]) for seg in self.segments]
        self.last_segment = None
        self.last_layer = None
        self.started = False
        self.finished = False
        self.last_fp = None
        self.last_fp_t = 0.0

    def _derive_sidecar(self, filename: str) -> Optional[str]:
        if not filename:
            return None
        base, _ext = os.path.splitext(filename)
        candidates = [base + ".nosf.json"]
        if not os.path.isabs(filename):
            candidates.extend(
                os.path.join(root, base + ".nosf.json")
                for root in (
                    "/home/pi/printer_data/gcodes",
                    os.path.expanduser("~/printer_data/gcodes"),
                )
            )
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    def _ensure_sidecar_for_filename(self, filename: str) -> None:
        if not filename or filename == self.last_filename:
            return
        self.last_filename = filename
        if self.sidecar_path:
            return
        path = self._derive_sidecar(filename)
        if path:
            self.attach_sidecar(path)

    def find_segment(self, file_position: int):
        if not self.segments:
            return None
        idx = bisect_right(self.segment_starts, int(file_position)) - 1
        if idx < 0:
            return None
        seg = self.segments[idx]
        if int(file_position) >= int(seg["byte_end"]):
            return None
        return seg

    def _state_time(self, state: dict) -> float:
        try:
            return float(state.get("eventtime", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _is_stale_fp(self, state: dict, fp: int) -> bool:
        now = self._state_time(state) or time.monotonic()
        if self.last_fp is None or fp != self.last_fp:
            self.last_fp = fp
            self.last_fp_t = now
            return False
        if state.get("print_state") == "printing" and now - self.last_fp_t >= STALE_FP_S:
            return True
        return False

    def _terminal_event(self, events: List[str]) -> None:
        if not self.finished:
            events.append("NOSF_TUNE:FINISH:0:0:0")
            self.finished = True
        self.started = False
        self.last_segment = None

    def update(self, state: dict) -> List[str]:
        events = []
        filename = state.get("filename", "") or state.get("source_gcode", "")
        self._ensure_sidecar_for_filename(filename)

        print_state = state.get("print_state")
        if print_state != self.last_print_state:
            if print_state == "printing" and self.attached and not self.started:
                events.append("NT:START")
                self.started = True
                self.finished = False
            elif print_state in ("complete", "cancelled", "error"):
                self._terminal_event(events)
            self.last_print_state = print_state

        if print_state == "paused":
            return events
        if not self.attached or print_state not in (None, "printing"):
            return events

        try:
            fp = int(state["file_position"])
            z_mm = float(state["z_mm"])
            v_extrude = float(state.get("v_extrude", 0.0))
        except (KeyError, TypeError, ValueError):
            return events

        if self._is_stale_fp(state, fp):
            return events
        seg = self.find_segment(fp)
        if not seg:
            self.last_segment = None
            return events
        if seg.get("skip"):
            return events
        if abs(z_mm - float(seg.get("z_mm", z_mm))) > Z_GUARD_MM:
            self.last_segment = None
            return events
        if v_extrude <= 0.0:
            return events

        if seg["layer"] != self.last_layer:
            events.append(f"NT:LAYER:{int(seg['layer'])}")
            self.last_layer = seg["layer"]

        prev = self.last_segment
        changed = (
            prev is None
            or seg["feature"] != prev["feature"]
            or int(seg["v_fil_bin"]) != int(prev["v_fil_bin"])
            or float(seg["width_mm"]) != float(prev["width_mm"])
            or float(seg["height_mm"]) != float(prev["height_mm"])
        )
        if changed:
            try:
                speed_factor = float(state.get("speed_factor", 1.0) or 1.0)
                extrude_factor = float(state.get("extrude_factor", 1.0) or 1.0)
            except (TypeError, ValueError):
                speed_factor = 1.0
                extrude_factor = 1.0
            v_fil = float(seg["v_fil_mm3_per_s"]) * speed_factor * extrude_factor
            events.append(
                f"NOSF_TUNE:{seg['feature']}:V{v_fil:.1f}:"
                f"W{float(seg['width_mm']):.2f}:H{float(seg['height_mm']):.2f}"
            )

        self.last_segment = seg
        return events
