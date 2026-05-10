#!/usr/bin/env python3
"""Stdlib tests for klipper_motion_tracker.py."""

import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(__file__))

import klipper_motion_tracker as tracker


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


def main():
    tests = [
        ("framing", test_framing_roundtrip),
        ("partial", test_partial_chunk),
        ("two-msg", test_two_messages_one_chunk),
        ("subscribe", test_subscribe_request_shape),
        ("disconnect", test_garbage_disconnect),
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
