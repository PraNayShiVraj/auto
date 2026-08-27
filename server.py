"""
The ONE Render service, running on the FREE plan. Does three jobs at once:

  1. Web server — serves /video/<file_id> so Instagram's API can fetch
     the video from a public URL (required by their Reels endpoint).
  2. Internal scheduler — a background thread that wakes up every 20s,
     checks the current time in SCHEDULE_TIMEZONE, and calls
     poster.run_slot(n) when it matches one of POST_TIMES.
  3. Self-pinger — a background thread that requests its own /health
     endpoint every SELF_PING_MINUTES. Render's free plan spins a web
     service down after ~15 min with no incoming HTTP traffic; this
     keeps traffic flowing so it never goes to sleep, which is what
     lets the scheduler thread above keep running 24/7 for $0.

     Note: this is a well-known, widely used workaround, not an
     official "always-on free" feature — Render doesn't contractually
     guarantee a self-pinged free service never sleeps. In practice it
     works reliably. Free plan usage is capped at 750 instance-hours/
     month account-wide; running this one service 24/7 uses ~720-744
     of those, so keep this as your only always-on free service.
"""
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import Flask, Response, abort

from drive_utils import get_drive_service, get_file_stream
from poster import run_slot, reset_state

app = Flask(__name__)

PORT = int(os.environ.get("PORT", 10000))
TIMEZONE = ZoneInfo(os.environ.get("SCHEDULE_TIMEZONE", "Asia/Kolkata"))
SELF_PING_MINUTES = int(os.environ.get("SELF_PING_MINUTES", "12"))

# Slot number -> "HH:MM" (24hr, in TIMEZONE). Edit these to change your
# posting times — no need to touch render.yaml or add cron jobs.
POST_TIMES = {
    1: "17:00",
    2: "18:00",
    3: "19:00",
    4: "20:00",
}

# In-memory guard so the same slot doesn't get triggered twice within
# the same minute (the loop checks every 20s). poster.run_slot() is
# also independently safe to call twice thanks to autopost_state.json,
# this is just to avoid pointless duplicate Drive round-trips.
_already_triggered_today = set()
_last_checked_date = None


def _scheduler_loop():
    global _already_triggered_today, _last_checked_date

    print(f"Scheduler thread started. Timezone={TIMEZONE}, slots={POST_TIMES}", flush=True)
    while True:
        try:
            now = datetime.now(TIMEZONE)
            date_key = now.strftime("%Y-%m-%d")
            hhmm = now.strftime("%H:%M")

            if date_key != _last_checked_date:
                _already_triggered_today = set()
                _last_checked_date = date_key

            for slot, target_time in POST_TIMES.items():
                # Normalize target_time to 2-digit HH:MM format (e.g. "4:20" -> "04:20")
                # so single-digit hour strings match datetime.strftime("%H:%M")
                try:
                    h, m = target_time.split(":")
                    norm_target = f"{int(h):02d}:{int(m):02d}"
                except Exception:
                    norm_target = target_time

                if hhmm == norm_target and slot not in _already_triggered_today:
                    print(f"[{now.isoformat()}] Triggering slot {slot} ({target_time})", flush=True)
                    _already_triggered_today.add(slot)
                    try:
                        result = run_slot(slot)
                        print(f"Slot {slot} result: {result}", flush=True)
                    except Exception as e:
                        print(f"Slot {slot} raised an exception: {e}", flush=True)
        except Exception as loop_err:
            # Never let the scheduler thread die silently.
            print(f"Scheduler loop error: {loop_err}", flush=True)

        time.sleep(20)


def _self_ping_loop():
    """Pings our own /health endpoint periodically so Render's free
    plan sees continuous traffic and never spins the service down."""
    # Give the server a few seconds to finish starting before the first ping.
    time.sleep(15)

    print(f"Self-ping thread started. Pinging every {SELF_PING_MINUTES} min.")
    while True:
        try:
            url = os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL")
            if url:
                resp = requests.get(f"{url.rstrip('/')}/health", timeout=30)
                print(f"[self-ping] {resp.status_code} at {datetime.now(TIMEZONE).isoformat()}")
            else:
                print("[self-ping] No public URL known yet, skipping this round.")
        except Exception as e:
            # Never let a failed ping kill the thread — just try again next time.
            print(f"[self-ping] failed: {e}")

        time.sleep(SELF_PING_MINUTES * 60)


@app.route("/")
def root():
    return "autopost server is running", 200


@app.route("/health")
def health():
    now = datetime.now(TIMEZONE)
    return {
        "ok": True,
        "time": now.isoformat(),
        "timezone": str(TIMEZONE),
        "post_times": POST_TIMES,
        "triggered_today": sorted(_already_triggered_today),
    }, 200


@app.route("/video/<file_id>")
def video(file_id):
    try:
        drive = get_drive_service()
        stream = get_file_stream(drive, file_id)
    except Exception as e:
        abort(404, description=str(e))

    return Response(stream.read(), mimetype="video/mp4")


@app.route("/reset")
def manual_reset():
    secret = os.environ.get("RUN_SECRET")
    from flask import request
    if secret and request.args.get("key") != secret:
        abort(403)

    global _already_triggered_today
    _already_triggered_today = set()
    result = reset_state()
    return result, 200


@app.route("/run/<int:slot>")
def manual_run(slot):
    """Manual trigger for testing, e.g. https://your-app.onrender.com/run/1?key=SECRET
    Protected by RUN_SECRET so randos can't trigger posts via a guessed URL.
    """
    secret = os.environ.get("RUN_SECRET")
    from flask import request
    if secret and request.args.get("key") != secret:
        abort(403)

    if slot not in POST_TIMES:
        abort(400, description=f"slot must be one of {list(POST_TIMES)}")

    result = run_slot(slot)
    return result, 200


def _start_background_threads_once():
    # Flask's reloader (debug mode) can import this module twice; guard
    # against starting duplicate threads. Not an issue in production
    # (gunicorn), but harmless to keep.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        threading.Thread(target=_scheduler_loop, daemon=True).start()
        threading.Thread(target=_self_ping_loop, daemon=True).start()


_start_background_threads_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
