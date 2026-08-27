"""
The actual "post one video" logic. Used to live in publisher.py and run
as 4 separate Render Cron Jobs. Now it's just a function called by the
scheduler thread inside server.py — one service, one process.

What run_slot(slot) does, each time it's called:
  1. Looks at today's date (Asia/Kolkata by default). If this is the
     first call today, it picks the NEXT 4 not-yet-ever-posted videos
     from your Drive folder (sorted by filename) and locks them in as
     "today's batch" — saved back into autopost_state.json in the same
     Drive folder.
  2. Takes the video for THIS slot number out of today's batch.
  3. Downloads it, uploads it to YouTube, and tells Instagram to
     publish it as a Reel (pulling the file from this same service's
     /video/<id> endpoint).
  4. Marks that slot as posted, so re-calls / retries never double-post.
  5. Deletes the source file from Drive once BOTH platforms have it.
"""
import os
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

import re
from drive_utils import get_drive_service, list_videos, download_file, delete_file, load_state, save_state, _find_state_file
from youtube_uploader import upload_video, update_video_description
from instagram_uploader import publish_reel


def parse_part_number(filename: str):
    """Extracts integer part number from filename (e.g. Part_2 -> 2)."""
    m = re.search(r'part[_\s-]*(\d+)', filename, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m2 = re.search(r'\b(\d+)\b', filename)
    if m2:
        return int(m2.group(1))
    return None

TIMEZONE = ZoneInfo(os.environ.get("SCHEDULE_TIMEZONE", "Asia/Kolkata"))
FOLDER_ID = os.environ["DRIVE_FOLDER_ID"]

TAGS = ["anime", "manhwa"]


def reset_state():
    """Resets state in Drive so a fresh batch is generated using natural sorting."""
    drive = get_drive_service()
    file_id = _find_state_file(drive, FOLDER_ID)
    if file_id:
        delete_file(drive, file_id)
        print("Reset state: deleted autopost_state.json from Drive.")
        return {"status": "reset_successful", "message": "State reset. Next run will pick Part 1."}
    return {"status": "reset_not_needed", "message": "No state file found."}


def today_str():
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d")


def _public_base_url():
    # Render sets this automatically for every web service — no need to
    # copy/paste your own URL into an env var like the old 5-service setup.
    url = os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        raise RuntimeError(
            "Could not determine this service's public URL. Set PUBLIC_BASE_URL manually."
        )
    return url.rstrip("/")


def build_todays_batch(drive, state, slot_count=10):
    """Picks the next N videos (by filename order) that have never been
    posted before, across all days. Returns list of {"id","name"}.
    """
    all_videos = list_videos(drive, FOLDER_ID)
    ever_posted = set(state.get("ever_posted_ids", []))
    remaining = [v for v in all_videos if v["id"] not in ever_posted]

    if len(remaining) < slot_count:
        print(
            f"WARNING: only {len(remaining)} unposted video(s) left in the Drive folder. "
            "Add more videos soon or slots will be skipped."
        )

    return remaining[:slot_count]


def run_slot(slot: int):
    """slot is 1..10. Safe to call more than once for the same
    slot/day — it no-ops if that slot was already posted today."""
    drive = get_drive_service()
    state = load_state(drive, FOLDER_ID)

    date_key = today_str()
    if state.get("date") != date_key:
        batch = build_todays_batch(drive, state, slot_count=10)
        state = {
            "date": date_key,
            "batch": [{"id": v["id"], "name": v["name"]} for v in batch],
            "posted_slots": [],
            "ever_posted_ids": state.get("ever_posted_ids", []),
        }
        save_state(drive, FOLDER_ID, state)
        print(f"Started new batch for {date_key}: {[v['name'] for v in batch]}")

    batch = state.get("batch", [])
    if slot > len(batch):
        print(f"No video queued for slot {slot} today (only {len(batch)} video(s) in today's batch). Skipping.")
        return {"status": "skipped", "reason": "no video for this slot today"}

    if slot in state.get("posted_slots", []):
        print(f"Slot {slot} was already posted today. Skipping to avoid a duplicate post.")
        return {"status": "already_posted"}

    video_meta = batch[slot - 1]
    video_id, video_name = video_meta["id"], video_meta["name"]
    caption = os.path.splitext(video_name)[0].replace("_", " ").replace("-", " ")
    hashtags = " ".join(f"#{t}" for t in TAGS)
    ig_caption = f"{caption}\n\n{hashtags}"

    part_num = parse_part_number(video_name)
    part_history = state.get("part_history", {})
    prev_yt_id = None
    yt_description = caption

    if part_num and part_num > 1:
        prev_part_num = part_num - 1
        prev_yt_id = part_history.get(str(prev_part_num))
        if prev_yt_id:
            yt_description = f"{caption}\n\n👈 Watch Part {prev_part_num}: https://youtube.com/watch?v={prev_yt_id}"
        ig_caption = f"{caption}\n\n👈 Watch Part {prev_part_num}\n\n{hashtags}"

    print(f"Slot {slot}: posting '{video_name}' ({video_id})")

    with tempfile.TemporaryDirectory() as tmp:
        local_path = os.path.join(tmp, video_name)
        download_file(drive, video_id, local_path)

        yt_err = None
        ig_err = None

        try:
            yt_id = upload_video(local_path, title=caption, description=yt_description, tags=TAGS)
            print(f"Uploaded to YouTube: https://youtube.com/watch?v={yt_id}")
        except Exception as e:
            yt_err = str(e)
            print(f"YouTube upload FAILED for slot {slot}: {e}", file=sys.stderr)
            yt_id = None

        try:
            public_video_url = f"{_public_base_url()}/video/{video_id}"
            ig_id = publish_reel(public_video_url, caption=ig_caption)
            print(f"Published to Instagram: media id {ig_id}")
        except Exception as e:
            ig_err = str(e)
            print(f"Instagram publish FAILED for slot {slot}: {e}", file=sys.stderr)
            ig_id = None

    if yt_id or ig_id:
        state.setdefault("posted_slots", []).append(slot)
        state.setdefault("ever_posted_ids", []).append(video_id)
        if yt_id and part_num:
            part_history[str(part_num)] = yt_id
            state["part_history"] = part_history
        save_state(drive, FOLDER_ID, state)

        # Update previous part's YouTube description to link forward to this new part!
        if yt_id and prev_yt_id and part_num:
            update_video_description(
                prev_yt_id,
                f"👉 Watch Next (Part {part_num}): https://youtube.com/watch?v={yt_id}"
            )
    else:
        print(f"Both uploads failed for slot {slot}; state left unchanged so it can be retried.", file=sys.stderr)
        return {
            "status": "failed",
            "youtube_error": yt_err,
            "instagram_error": ig_err,
            "video": video_name,
        }

    if yt_id and ig_id:
        try:
            delete_file(drive, video_id)
            print(f"Deleted '{video_name}' from Drive (posted to both platforms).")
        except Exception as e:
            print(f"Posted successfully but failed to delete '{video_name}' from Drive: {e}", file=sys.stderr)
    else:
        print(f"Kept '{video_name}' in Drive since only one platform succeeded (yt={bool(yt_id)}, ig={bool(ig_id)}).")

    return {
        "status": "posted",
        "youtube": bool(yt_id),
        "instagram": bool(ig_id),
        "youtube_error": yt_err,
        "instagram_error": ig_err,
        "video": video_name,
    }


def sync_uploaded_videos_history():
    """Scans channel videos, registers their part numbers in part_history,
    updates categories to Entertainment (24), and links prev/next parts.
    """
    from youtube_uploader import get_my_uploaded_videos, update_video_description
    drive = get_drive_service()
    state = load_state(drive, FOLDER_ID)
    part_history = state.get("part_history", {})

    uploaded_videos = get_my_uploaded_videos()
    parsed_parts = {}

    for v in uploaded_videos:
        part_num = parse_part_number(v["title"])
        if part_num:
            parsed_parts[part_num] = v
            part_history[str(part_num)] = v["id"]

    state["part_history"] = part_history
    save_state(drive, FOLDER_ID, state)

    # Link prev/next parts and ensure Category 24 on YouTube
    sorted_parts = sorted(parsed_parts.keys())
    results = {}

    for p in sorted_parts:
        curr_video = parsed_parts[p]
        curr_id = curr_video["id"]
        extra_lines = []

        if p > 1 and (p - 1) in parsed_parts:
            prev_video = parsed_parts[p - 1]
            extra_lines.append(f"👈 Watch Part {p - 1}: https://youtube.com/watch?v={prev_video['id']}")

        if (p + 1) in parsed_parts:
            next_video = parsed_parts[p + 1]
            extra_lines.append(f"👉 Watch Next (Part {p + 1}): https://youtube.com/watch?v={next_video['id']}")

        extra_text = "\n\n".join(extra_lines) if extra_lines else ""
        res = update_video_description(curr_id, extra_text)
        results[f"part_{p}"] = {
            "id": curr_id,
            "title": curr_video["title"],
            "update_result": res
        }

    return {
        "status": "sync_completed",
        "synced_parts": sorted_parts,
        "results": results,
        "part_history": part_history
    }
