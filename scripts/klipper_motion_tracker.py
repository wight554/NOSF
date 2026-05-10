#!/usr/bin/env python3
"""Klipper API motion tracker helpers for NOSF Phase 2.10.

Pure stdlib. The Klipper API server speaks JSON-RPC-ish objects over a
Unix domain socket, with each JSON object terminated by ASCII ETX (0x03).
"""

import json
import select
import socket
from collections import deque
from typing import Dict, List, Optional


ETX = b"\x03"
DEFAULT_UDS_PATH = "/tmp/klippy_uds"
SUBSCRIBE_OBJECTS = {
    "motion_report": ["live_position", "live_velocity", "live_extruder_velocity"],
    "gcode_move": ["speed_factor", "extrude_factor", "position"],
    "print_stats": ["state", "filename", "current_layer"],
    "virtual_sdcard": ["is_active", "file_position", "file_size"],
    "webhooks": ["state"],
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
