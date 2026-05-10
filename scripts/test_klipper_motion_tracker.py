#!/usr/bin/env python3
"""Stdlib tests for klipper_motion_tracker.py."""

import json
import os
import shutil
import socket
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

import gcode_marker
import klipper_motion_tracker as tracker

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
ORCA_FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "orca_sample.gcode")


def make_client_pair():
    client_sock, server_sock = socket.socketpair()
    client_sock.setblocking(False)
    client = tracker.KlipperApiClient()
    client.sock = client_sock
    return client, server_sock


def send_msg(sock, obj):
    sock.sendall(json.dumps(obj).encode("utf-8") + tracker.ETX)


def recv_framed(sock):
    data = b""
    while tracker.ETX not in data:
        data += sock.recv(4096)
    raw, _rest = data.split(tracker.ETX, 1)
    return json.loads(raw.decode("utf-8"))


def test_framing_roundtrip():
    client, peer = make_client_pair()
    try:
        expected = {"id": 7, "result": {"objects": ["motion_report"]}}
        send_msg(peer, expected)
        got = client.poll(0.1)
        assert got == expected, got
        return "client parses one ETX-framed JSON object"
    finally:
        client.close()
        peer.close()


def test_partial_chunk():
    client, peer = make_client_pair()
    try:
        payload = json.dumps({"id": 1, "result": {"ok": True}}).encode("utf-8") + tracker.ETX
        split = len(payload) // 2
        peer.sendall(payload[:split])
        assert client.poll(0.1) is None
        peer.sendall(payload[split:])
        got = client.poll(0.1)
        assert got["result"]["ok"] is True, got
        return "client reassembles JSON split across recv chunks"
    finally:
        client.close()
        peer.close()


def test_two_messages_one_chunk():
    client, peer = make_client_pair()
    try:
        first = {"id": 1, "result": "a"}
        second = {"id": 2, "result": "b"}
        peer.sendall(
            json.dumps(first).encode("utf-8")
            + tracker.ETX
            + json.dumps(second).encode("utf-8")
            + tracker.ETX
        )
        assert client.poll(0.1) == first
        assert client.poll(0.1) == second
        return "client queues two messages from one recv chunk"
    finally:
        client.close()
        peer.close()


def test_subscribe_request_shape():
    client, peer = make_client_pair()
    try:
        req_id = client.subscribe(tracker.SUBSCRIBE_OBJECTS)
        msg = recv_framed(peer)
        assert msg["id"] == req_id, msg
        assert msg["method"] == "objects/subscribe", msg
        params = msg["params"]
        assert params["objects"] == tracker.SUBSCRIBE_OBJECTS, params
        assert params["response_template"] == {}, params
        return "subscribe request has Klipper objects/subscribe shape"
    finally:
        client.close()
        peer.close()


def test_garbage_disconnect():
    client, peer = make_client_pair()
    try:
        peer.sendall(b'{"id": 3')
        assert client.poll(0.1) is None
        peer.close()
        try:
            client.poll(0.1)
        except ConnectionResetError:
            return "client raises on socket close with partial frame buffered"
        assert False, "expected ConnectionResetError"
    finally:
        client.close()


def build_fixture_sidecar():
    td = tempfile.TemporaryDirectory()
    gcode_path = os.path.join(td.name, "orca_sample.gcode")
    sidecar_path = os.path.join(td.name, "orca_sample.nosf.json")
    shutil.copyfile(ORCA_FIXTURE, gcode_path)
    data = gcode_marker.build_sidecar(gcode_path, sidecar_path, 1.75)
    return td, gcode_path, sidecar_path, data


def state_for(seg, gcode_path, eventtime=1.0, print_state="printing", v_extrude=1.0):
    fp = int((seg["byte_start"] + seg["byte_end"]) // 2)
    return {
        "filename": gcode_path,
        "print_state": print_state,
        "file_position": fp,
        "z_mm": seg["z_mm"],
        "v_extrude": v_extrude,
        "speed_factor": 1.0,
        "extrude_factor": 1.0,
        "eventtime": eventtime,
    }


def test_layer_transition_emits_event():
    td, gcode_path, _sidecar_path, data = build_fixture_sidecar()
    try:
        matcher = tracker.SegmentMatcher(os.path.join(td.name, "orca_sample.nosf.json"))
        events = matcher.update(state_for(data["segments"][0], gcode_path))
        assert events[0] == "NT:START", events
        assert "NT:LAYER:0" in events, events
        assert any(e.startswith("NOSF_TUNE:Outer_wall:") for e in events), events
        return "printing + first segment emits START, LAYER, feature"
    finally:
        td.cleanup()


def test_feature_change_emits_nosf_tune():
    td, gcode_path, sidecar_path, data = build_fixture_sidecar()
    try:
        matcher = tracker.SegmentMatcher(sidecar_path)
        first = data["segments"][0]
        next_feature = next(seg for seg in data["segments"] if seg["feature"] != first["feature"] and not seg.get("skip"))
        matcher.update(state_for(first, gcode_path, eventtime=1.0))
        events = matcher.update(state_for(next_feature, gcode_path, eventtime=2.0))
        assert any(e.startswith(f"NOSF_TUNE:{next_feature['feature']}:") for e in events), events
        return "feature transition emits NOSF_TUNE marker"
    finally:
        td.cleanup()


def test_v_fil_bin_unchanged_no_event():
    td, gcode_path, sidecar_path, data = build_fixture_sidecar()
    try:
        matcher = tracker.SegmentMatcher(sidecar_path)
        seg = data["segments"][0]
        matcher.update(state_for(seg, gcode_path, eventtime=1.0))
        events = matcher.update(state_for(seg, gcode_path, eventtime=1.5))
        assert events == [], events
        return "same segment produces no duplicate feature event"
    finally:
        td.cleanup()


def test_retract_no_event():
    td, gcode_path, sidecar_path, data = build_fixture_sidecar()
    try:
        matcher = tracker.SegmentMatcher(sidecar_path)
        matcher.update({"filename": gcode_path, "print_state": "printing", "eventtime": 0.1})
        events = matcher.update(state_for(data["segments"][0], gcode_path, eventtime=1.0, v_extrude=-0.1))
        assert events == [], events
        return "negative extruder velocity suppresses segment events"
    finally:
        td.cleanup()


def test_pause_resume_segment_state_survives():
    td, gcode_path, sidecar_path, data = build_fixture_sidecar()
    try:
        matcher = tracker.SegmentMatcher(sidecar_path)
        first = data["segments"][0]
        second = next(seg for seg in data["segments"] if seg["feature"] != first["feature"] and not seg.get("skip"))
        matcher.update(state_for(first, gcode_path, eventtime=1.0))
        assert matcher.update({"filename": gcode_path, "print_state": "paused", "eventtime": 2.0}) == []
        events = matcher.update(state_for(second, gcode_path, eventtime=3.0))
        assert any(e.startswith(f"NOSF_TUNE:{second['feature']}:") for e in events), events
        return "pause emits no marker and resume keeps matcher state"
    finally:
        td.cleanup()


def test_filename_change_loads_new_sidecar():
    td, gcode_path, sidecar_path, data = build_fixture_sidecar()
    try:
        matcher = tracker.SegmentMatcher()
        events = matcher.update(state_for(data["segments"][0], gcode_path, eventtime=1.0))
        assert matcher.attached
        assert matcher.sidecar_path == sidecar_path, matcher.sidecar_path
        assert "NT:START" in events, events
        return "filename derives and attaches colocated sidecar"
    finally:
        td.cleanup()


def main():
    tests = [
        ("framing", test_framing_roundtrip),
        ("partial", test_partial_chunk),
        ("two-msg", test_two_messages_one_chunk),
        ("subscribe", test_subscribe_request_shape),
        ("disconnect", test_garbage_disconnect),
        ("layer-event", test_layer_transition_emits_event),
        ("feature", test_feature_change_emits_nosf_tune),
        ("dedupe", test_v_fil_bin_unchanged_no_event),
        ("retract", test_retract_no_event),
        ("pause", test_pause_resume_segment_state_survives),
        ("filename", test_filename_change_loads_new_sidecar),
    ]
    print(f"{'case':<14} result")
    print(f"{'-' * 14} {'-' * 40}")
    for name, fn in tests:
        try:
            detail = fn()
        except AssertionError as exc:
            print(f"{name:<14} FAIL {exc}")
            return 1
        except Exception as exc:
            print(f"{name:<14} ERROR {exc}")
            return 1
        print(f"{name:<14} PASS {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
