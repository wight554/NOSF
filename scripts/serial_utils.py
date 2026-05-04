#!/usr/bin/env python3
"""Serial-port helper utilities for NOSF scripts."""

from __future__ import annotations

import glob
import os
from typing import List, Optional


def list_serial_ports() -> List[str]:
    """Return candidate serial ports in priority order."""
    ports: List[str] = []

    # Prefer pyserial enumeration when available because it works across OSes.
    try:
        from serial.tools import list_ports  # type: ignore

        discovered = [p.device for p in list_ports.comports()]
        ports.extend(discovered)
    except Exception:
        pass

    # Fallback glob patterns for common platforms.
    patterns = [
        "/dev/cu.usbmodem*",  # macOS CDC-ACM
        "/dev/cu.usbserial*",  # macOS USB-serial
        "/dev/ttyACM*",  # Linux CDC-ACM
        "/dev/ttyUSB*",  # Linux USB-serial
        "/dev/tty.usbmodem*",  # some macOS setups
        "COM*",  # Windows (best-effort fallback)
    ]

    for pattern in patterns:
        for port in glob.glob(pattern):
            ports.append(port)

    # De-duplicate while preserving order.
    seen = set()
    unique_ports: List[str] = []
    for port in ports:
        normalized = os.path.normcase(port)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_ports.append(port)

    return unique_ports


def find_port(preferred: Optional[str] = None) -> Optional[str]:
    """Return preferred port if available, else first discovered port."""
    ports = list_serial_ports()
    if not ports:
        return None

    if preferred:
        for port in ports:
            if port == preferred:
                return port

    return ports[0]
