# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""Best-effort bridge to a Flipper Zero connected to this same host over USB
serial. Ported from the host's ~/.local/bin/flipper-tool.py (already proven
against this exact Unleashed-firmware device) down to just the one thing
chaos needs: pushing a short status line to
/ext/apps_data/chaos_relay/status.txt, which the CHAOS Relay Flipper app
(~/flipper-apps/chaos_relay.c) polls and displays on-screen.

Requires the `pyserial` package (see requirements.txt) and /dev/flipper to
be visible from wherever this process runs — true today because the
Arduino App runtime bind-mounts the host's /dev wholesale into the app
container, but not verified end-to-end since chaos hasn't had a full
`app start` yet (blocked on a USB webcam for the other Brick). Every
function here fails soft: no Flipper attached, or a mid-push conflict with
something else holding the port, just means the push is skipped.
"""

import time

try:
    import serial
except ImportError:  # pyserial not installed — bridge becomes a no-op
    serial = None

PORT = "/dev/flipper"
STATUS_REMOTE_PATH = "/ext/apps_data/chaos_relay/status.txt"
MAX_STATUS_LEN = 120
BAUDRATE = 9600


def _read_until_prompt(ser, overall_timeout=5) -> bytes:
    end = time.time() + overall_timeout
    buf = b""
    while time.time() < end:
        buf += ser.read(4096)
        if buf.rstrip().endswith(b">:"):
            break
    return buf


def push_status(text: str) -> bool:
    """Write `text` to the Flipper's CHAOS Relay status file.

    Returns True on success, False if no Flipper is attached or the push
    failed for any reason — callers should treat this as optional, never
    block on it.
    """
    if serial is None:
        return False

    text = text[:MAX_STATUS_LEN]
    try:
        ser = serial.Serial(PORT, baudrate=BAUDRATE, timeout=1)
    except Exception:
        return False

    try:
        ser.write(b"\n\n\r")
        _read_until_prompt(ser)

        # `storage write` appends to an existing file rather than truncating
        # it, so remove any previous status first (a no-op if none exists).
        ser.reset_input_buffer()
        ser.write(f"storage remove {STATUS_REMOTE_PATH}\r".encode())
        time.sleep(0.2)
        _read_until_prompt(ser)

        ser.reset_input_buffer()
        ser.write(f"storage write {STATUS_REMOTE_PATH}\r".encode())
        time.sleep(0.3)
        ser.read(ser.in_waiting or 0)  # discard the "Just write..." prompt
        ser.write(text.encode())
        time.sleep(0.4)
        ser.write(b"\x03")  # Ctrl+C ends write mode
        time.sleep(0.3)
        _read_until_prompt(ser)
        return True
    except Exception:
        return False
    finally:
        ser.close()
