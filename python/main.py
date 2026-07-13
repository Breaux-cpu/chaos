# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""CHAOS — recon companion for the Arduino UNO Q.

Scans QR codes / barcodes with the camera, runs an authorized-use pentest
toolkit (nmap/masscan/whois/nikto/gobuster/sqlmap/hydra/tcpdump/tshark/
aircrack-ng) on demand, mirrors status on the LED matrix (scanning / match /
alert), and streams everything to the web dashboard on :7000.
"""

import json
import os
import threading
import time

from arduino.app_utils import App, Bridge, Logger
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.camera_code_detection import CameraCodeDetection, Detection
from arduino.app_bricks.dbstorage_sqlstore import SQLStore

import pentest
import flipper_bridge

MATCH_DISPLAY_SECONDS = 1.5
ALERT_DISPLAY_SECONDS = 3.0
MAX_RECENT_SCANS = 25

# If set (Brick Configuration env var), pentest actions must include this
# token in the WebSocket payload. Left unset, the dashboard's pentest panel
# is reachable by anyone who can reach port 7000 — see README for why that
# matters and how to lock it down.
PENTEST_TOKEN = os.environ.get("CHAOS_PENTEST_TOKEN")

_state_lock = threading.Lock()
_match_until = 0.0
_alert_until = 0.0
recent_scans = []  # most recent first


def get_led_state():
    """Polled by the sketch every loop() to pick which frame to draw."""
    with _state_lock:
        now = time.time()
        if now < _alert_until:
            return "alert"
        if now < _match_until:
            return "match"
        return "scanning"


def _flash_match(duration=MATCH_DISPLAY_SECONDS):
    global _match_until
    with _state_lock:
        _match_until = time.time() + duration


def _flash_alert(duration=ALERT_DISPLAY_SECONDS):
    global _alert_until
    with _state_lock:
        _alert_until = time.time() + duration


def _relay_to_flipper(text: str):
    """Best-effort, non-blocking: push a status line to a connected Flipper
    (CHAOS Relay app). A missing/busy Flipper is not an error for chaos."""
    threading.Thread(target=flipper_bridge.push_status, args=(text,), daemon=True).start()


# --- QR / barcode scanning -----------------------------------------------


def on_code_detected(frame, detection: Detection):
    entry = {
        "type": detection.type,
        "content": detection.content,
        "timestamp": time.time(),
    }
    recent_scans.insert(0, entry)
    del recent_scans[MAX_RECENT_SCANS:]
    Logger.info(f"Detected {detection.type}: {detection.content}")

    try:
        store.store("scans", entry)
    except Exception as e:
        Logger.error(f"Failed to persist scan: {e}")

    _flash_match()
    _relay_to_flipper(f"{detection.type}: {detection.content}")
    ui.send_message("scan", entry)


def on_camera_error(error: Exception):
    Logger.error(f"Camera error: {error}")
    _flash_alert()
    ui.send_message("error", {"message": str(error)})


def list_scans():
    return {"scans": recent_scans}


def history_scans():
    """Persistent scan history, unlike /api/scans which is just this
    session's in-memory list — survives an app restart."""
    return {"scans": store.read("scans", order_by="timestamp DESC", limit=100)}


# --- Pentest toolkit -------------------------------------------------------


def _persist_job(job: pentest.Job):
    try:
        store.store(
            "jobs",
            {
                "job_id": job.id,
                "tool": job.tool,
                "target": job.target,
                "status": job.status,
                "returncode": job.returncode if job.returncode is not None else -1,
                "started_at": job.started_at,
                "finished_at": job.finished_at or 0.0,
                "output": job.output,
                "extra": json.dumps(job.extra),
            },
        )
    except Exception as e:
        Logger.error(f"Failed to persist job {job.id}: {e}")


def _on_job_update(job: pentest.Job):
    ui.send_message("job_update", pentest.to_dict(job))
    if job.status == "done":
        _flash_match()
        _relay_to_flipper(f"{job.tool} done: {job.target}")
        _persist_job(job)
    elif job.status == "error":
        _flash_alert()
        _relay_to_flipper(f"{job.tool} FAILED: {job.target}")
        _persist_job(job)


def on_pentest_run(sid, data):
    if PENTEST_TOKEN and data.get("token") != PENTEST_TOKEN:
        return {"error": "unauthorized"}

    tool = data.get("tool")
    try:
        if tool == "nmap":
            job = pentest.nmap_scan(data.get("target", ""), data.get("profile", "quick"), _on_job_update)
        elif tool == "masscan":
            job = pentest.masscan_scan(data.get("target", ""), data.get("ports", "1-1000"), _on_job_update)
        elif tool == "whois":
            job = pentest.whois_lookup(data.get("target", ""), _on_job_update)
        elif tool == "nikto":
            job = pentest.nikto_scan(data.get("target", ""), _on_job_update)
        elif tool == "gobuster":
            job = pentest.gobuster_scan(data.get("target", ""), _on_job_update)
        elif tool == "sqlmap":
            job = pentest.sqlmap_scan(data.get("target", ""), _on_job_update)
        elif tool == "hydra":
            job = pentest.hydra_attack(
                data.get("target", ""), data.get("service", "ssh"), _on_job_update, port=data.get("port")
            )
        elif tool == "tcpdump":
            job = pentest.tcpdump_capture(
                data.get("interface", "wlan0"),
                int(data.get("duration", 15)),
                data.get("filter", "all"),
                _on_job_update,
            )
        elif tool == "tshark":
            job = pentest.tshark_capture(
                data.get("interface", "wlan0"),
                int(data.get("duration", 15)),
                data.get("filter", "all"),
                _on_job_update,
            )
        elif tool == "wifi_scan":
            job = pentest.wifi_scan(
                data.get("interface", "wlan0"),
                int(data.get("duration", 30)),
                _on_job_update,
            )
        elif tool == "wifi_deauth":
            job = pentest.wifi_deauth(
                data.get("interface", "wlan0mon"),
                data.get("bssid", ""),
                data.get("client_mac", ""),
                int(data.get("count", 5)),
                _on_job_update,
            )
        else:
            return {"error": f"Unknown tool: {tool}"}
    except (ValueError, TypeError) as e:
        return {"error": str(e)}

    Logger.info(f"pentest job started: {job.tool} -> {job.target} (id={job.id})")
    return {"job_id": job.id, "status": job.status}


def list_jobs():
    return {"jobs": pentest.list_jobs()}


def history_jobs():
    """Persistent job history (completed/errored only) — survives an app
    restart, unlike /api/jobs which only knows about this session's jobs."""
    return {"jobs": store.read("jobs", order_by="finished_at DESC", limit=100)}


# --- wiring ------------------------------------------------------------

store = SQLStore("chaos.db")

ui = WebUI()
ui.expose_api("GET", "/api/scans", list_scans)
ui.expose_api("GET", "/api/jobs", list_jobs)
ui.expose_api("GET", "/api/history/scans", history_scans)
ui.expose_api("GET", "/api/history/jobs", history_jobs)
ui.on_connect(lambda sid: ui.send_message("scan_history", {"scans": recent_scans}))
ui.on_message("pentest_run", on_pentest_run)

if not PENTEST_TOKEN:
    Logger.warning(
        "CHAOS_PENTEST_TOKEN is not set — the pentest panel on :7000 is unauthenticated. "
        "Set it in Brick Configuration, and if this board is reachable beyond your LAN "
        "(e.g. over a public IPv6 address), firewall port 7000 to trusted interfaces only."
    )

detector = CameraCodeDetection()  # also initializes the camera
detector.on_detect(on_code_detected)
detector.on_error(on_camera_error)

# The sketch calls this every loop() to decide which LED matrix frame to draw.
Bridge.provide("get_led_state", get_led_state)

App.run()
