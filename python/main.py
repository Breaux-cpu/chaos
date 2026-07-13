# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""CHAOS — recon companion for the Arduino UNO Q.

Scans QR codes / barcodes with the camera, runs an authorized-use pentest
toolkit (nmap/masscan/whois/nikto/gobuster/sqlmap/hydra/tcpdump/tshark/
aircrack-ng) on demand, mirrors status on the LED matrix (scanning / match /
alert), streams everything to the web dashboard on :7000, and optionally
pushes alerts to a Telegram bot.
"""

import json
import os
import threading
import time
from io import BytesIO

from arduino.app_utils import App, Bridge, Logger
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.camera_code_detection import CameraCodeDetection, Detection
from arduino.app_bricks.dbstorage_sqlstore import SQLStore
from arduino.app_bricks.telegram_bot import TelegramBot, Sender, Message
from arduino.app_bricks.object_detection import ObjectDetection
from PIL import Image

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


def _parse_user_ids(raw):
    """Parse CHAOS_TELEGRAM_USER_IDS ('111, 222') into a list of ints,
    skipping anything non-numeric with a warning rather than crashing."""
    ids = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            Logger.warning(f"Ignoring non-integer Telegram user id: {part!r}")
    return ids


# Telegram alerts are optional. The bot is only started if TELEGRAM_BOT_TOKEN
# is set (Brick Configuration). CHAOS_TELEGRAM_USER_IDS optionally restricts
# who can subscribe/read — without it, anyone who finds the bot can pull your
# scan and pentest-job data, so set it.
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_USER_IDS = _parse_user_ids(os.environ.get("CHAOS_TELEGRAM_USER_IDS"))

_state_lock = threading.Lock()
_match_until = 0.0
_alert_until = 0.0
recent_scans = []  # most recent first

# chat_ids that asked (via /start) to receive push alerts. Backed by the
# "subscribers" table so they survive a restart; the set is lazy-loaded from
# disk on first use (see _ensure_subscribers_loaded) to avoid reading the DB
# before App.run() has finished bringing the storage brick up.
bot = None
_subscribers = set()
_subscribers_lock = threading.Lock()
_subscribers_loaded = False


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


# --- Telegram alerts ------------------------------------------------------


def _ensure_subscribers_loaded():
    """Populate the in-memory subscriber set from disk once, on first use.
    Deferred (rather than done at import time) so the read happens well after
    App.run() has started the storage brick."""
    global _subscribers_loaded
    with _subscribers_lock:
        if _subscribers_loaded:
            return
        try:
            for row in store.read("subscribers", limit=1000):
                _subscribers.add(int(row["chat_id"]))
        except Exception as e:
            Logger.error(f"Failed to load Telegram subscribers: {e}")
        _subscribers_loaded = True


def _add_subscriber(chat_id: int, first_name: str) -> bool:
    """Register a chat for push alerts. Returns True if newly added."""
    _ensure_subscribers_loaded()
    with _subscribers_lock:
        is_new = chat_id not in _subscribers
        _subscribers.add(chat_id)
    if is_new:
        try:
            store.store(
                "subscribers",
                {"chat_id": chat_id, "first_name": first_name or "", "added_at": time.time()},
            )
        except Exception as e:
            Logger.error(f"Failed to persist Telegram subscriber {chat_id}: {e}")
    return is_new


def _remove_subscriber(chat_id: int):
    _ensure_subscribers_loaded()
    with _subscribers_lock:
        _subscribers.discard(chat_id)
    try:
        # chat_id is coerced to int, so interpolating it into the WHERE clause
        # carries no injection risk.
        store.delete("subscribers", f"chat_id = {int(chat_id)}")
    except Exception as e:
        Logger.error(f"Failed to remove Telegram subscriber {chat_id}: {e}")


def _notify_telegram(text: str):
    """Best-effort, non-blocking push of an alert line to every subscriber.
    No bot configured or no subscribers => silent no-op; a per-chat send
    failure is logged but never propagated to chaos's hot path."""
    if bot is None:
        return

    def _send():
        _ensure_subscribers_loaded()
        with _subscribers_lock:
            targets = list(_subscribers)
        for chat_id in targets:
            try:
                bot.send_message(chat_id, text)
            except Exception as e:
                Logger.error(f"Telegram push to {chat_id} failed: {e}")

    threading.Thread(target=_send, daemon=True).start()


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
    _notify_telegram(f"🌀 Scan · {detection.type}: {detection.content}")
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
        _notify_telegram(f"✅ {job.tool} done · {job.target}")
        _persist_job(job)
    elif job.status == "error":
        _flash_alert()
        _relay_to_flipper(f"{job.tool} FAILED: {job.target}")
        _notify_telegram(f"❌ {job.tool} FAILED · {job.target}")
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


def _seed_dashboard():
    """Push persisted scan/job history to the dashboard on connect, so the
    lists aren't empty until the next live event (including after a restart).
    The DB reads happen here — driven by a browser connecting — not at import,
    so storage is fully up by the time this runs. Falls back to the in-memory
    session list if a read fails."""
    try:
        scans = store.read("scans", order_by="timestamp DESC", limit=100)
    except Exception as e:
        Logger.error(f"Failed to seed scans from history: {e}")
        scans = recent_scans
    ui.send_message("scan_history", {"scans": scans})

    try:
        rows = store.read("jobs", order_by="finished_at DESC", limit=100)
    except Exception as e:
        Logger.error(f"Failed to seed jobs from history: {e}")
        return
    # Persisted rows use job_id; the dashboard's job renderer keys on id.
    jobs = [
        {"id": r["job_id"], "tool": r["tool"], "target": r["target"], "status": r["status"], "output": r["output"]}
        for r in rows
    ]
    ui.send_message("job_history", {"jobs": jobs})


# --- Telegram commands ----------------------------------------------------
#
# Alerts are outbound, but Telegram won't let a bot message someone who hasn't
# messaged it first — so /start captures the chat_id we push to.


def tg_start(sender: Sender, message: Message):
    is_new = _add_subscriber(sender.chat_id, sender.first_name)
    if is_new:
        sender.reply(
            f"🌀 CHAOS alerts on, {sender.first_name}. "
            "You'll get every QR/barcode scan and pentest job result here.\n\n"
            "/status — current state\n/scans — recent scans\n"
            "/jobs — recent pentest jobs\n/stop — turn alerts off\n\n"
            "📷 Send me a photo and I'll run object detection on it."
        )
    else:
        sender.reply("🌀 You're already subscribed. /help for commands.")


def tg_stop(sender: Sender, message: Message):
    _remove_subscriber(sender.chat_id)
    sender.reply("🔕 Alerts off. Send /start to turn them back on.")


def tg_help(sender: Sender, message: Message):
    sender.reply(
        "🌀 *CHAOS*\n\n"
        "/start — subscribe to alerts\n"
        "/stop — unsubscribe\n"
        "/status — current LED state + counts\n"
        "/scans — last few scans\n"
        "/jobs — last few pentest jobs\n\n"
        "📷 Send a photo to run object detection on it."
    )


def tg_status(sender: Sender, message: Message):
    running = [j for j in pentest.list_jobs() if j["status"] == "running"]
    sender.reply(
        f"State: {get_led_state()}\n"
        f"Scans this session: {len(recent_scans)}\n"
        f"Running jobs: {len(running)}"
    )


def tg_scans(sender: Sender, message: Message):
    scans = list(recent_scans[:5])
    if not scans:
        sender.reply("No scans yet.")
        return
    lines = [f"• {s['type']}: {s['content']}" for s in scans]
    sender.reply("Recent scans:\n" + "\n".join(lines))


def tg_jobs(sender: Sender, message: Message):
    jobs = pentest.list_jobs()[:5]
    if not jobs:
        sender.reply("No pentest jobs yet.")
        return
    lines = [f"• {j['tool']} {j['target']} — {j['status']}" for j in jobs]
    sender.reply("Recent jobs:\n" + "\n".join(lines))


def tg_photo(sender: Sender, message: Message, photo: bytes, filename: str, size: int):
    """Run object detection on a photo the user sends and reply with the
    annotated image plus a labelled summary — device ID without needing the
    board's own camera. Mirrors the object_detection Brick examples."""
    sender.reply("🔍 Detecting objects…")
    try:
        image = Image.open(BytesIO(photo))
        results = object_detection.detect(image, confidence=0.4)
    except Exception as e:
        Logger.error(f"Object detection failed on Telegram photo: {e}")
        sender.reply("❌ Couldn't process that image.")
        return

    detections = (results or {}).get("detection", [])
    if not detections:
        sender.reply("No objects detected.")
        return

    top = sorted(detections, key=lambda d: d.get("confidence", 0), reverse=True)[:6]
    summary = ", ".join(f"{d.get('class_name', '?')} ({d.get('confidence', 0):.2f})" for d in top)
    caption = f"✅ Found {len(detections)}: {summary}"

    annotated = None
    try:
        annotated = object_detection.draw_bounding_boxes(image, results)
    except Exception as e:
        Logger.error(f"draw_bounding_boxes failed: {e}")

    if annotated is not None:
        out = BytesIO()
        annotated.save(out, format="PNG")
        if sender.reply_photo(out.getvalue(), caption):
            return
    # No annotated image (or the photo send failed): fall back to text.
    sender.reply(caption)


# --- wiring ------------------------------------------------------------

store = SQLStore("chaos.db")

ui = WebUI()
ui.expose_api("GET", "/api/scans", list_scans)
ui.expose_api("GET", "/api/jobs", list_jobs)
ui.expose_api("GET", "/api/history/scans", history_scans)
ui.expose_api("GET", "/api/history/jobs", history_jobs)
ui.on_connect(lambda sid: _seed_dashboard())
ui.on_message("pentest_run", on_pentest_run)

if not PENTEST_TOKEN:
    Logger.warning(
        "CHAOS_PENTEST_TOKEN is not set — the pentest panel on :7000 is unauthenticated. "
        "Set it in Brick Configuration, and if this board is reachable beyond your LAN "
        "(e.g. over a public IPv6 address), firewall port 7000 to trusted interfaces only."
    )

object_detection = ObjectDetection()  # runs on photos sent to the Telegram bot

if TELEGRAM_TOKEN:
    bot = TelegramBot(whitelist_user_ids=TELEGRAM_USER_IDS or None)
    bot.add_command("start", tg_start, "Subscribe to CHAOS alerts")
    bot.add_command("stop", tg_stop, "Unsubscribe from alerts")
    bot.add_command("status", tg_status, "Current state and counts")
    bot.add_command("scans", tg_scans, "Recent scans")
    bot.add_command("jobs", tg_jobs, "Recent pentest jobs")
    bot.add_command("help", tg_help, "Show commands")
    bot.on_photo(tg_photo)  # send a photo -> object detection reply
    if not TELEGRAM_USER_IDS:
        Logger.warning(
            "TELEGRAM_BOT_TOKEN is set but CHAOS_TELEGRAM_USER_IDS is not — anyone who "
            "finds the bot can subscribe and read your scan/job data. Set CHAOS_TELEGRAM_USER_IDS "
            "(comma-separated Telegram user IDs) to lock it down."
        )
    else:
        Logger.info(f"Telegram alerts enabled for {len(TELEGRAM_USER_IDS)} whitelisted user(s).")
else:
    Logger.info("TELEGRAM_BOT_TOKEN not set — Telegram alerts disabled.")

detector = CameraCodeDetection()  # also initializes the camera
detector.on_detect(on_code_detected)
detector.on_error(on_camera_error)

# The sketch calls this every loop() to decide which LED matrix frame to draw.
Bridge.provide("get_led_state", get_led_state)

App.run()
