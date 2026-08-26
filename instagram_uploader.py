"""
Instagram Graph API — publishes a Reel from a PUBLIC video URL.

Requirements on the Instagram side (one-time setup, done in Meta's
dashboard, not in code):
  - Your Instagram account must be a Business or Creator account.
  - It must be linked to a Facebook Page you manage.
  - You need a long-lived Page/IG access token with
    instagram_content_publish permission, and your app must be either
    in Development mode (fine while you're the only user) or reviewed
    by Meta for that permission.
"""
import os
import time

import requests

GRAPH_VERSION = "v21.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"


def publish_reel(video_url: str, caption: str = "", max_wait_seconds: int = 300):
    ig_user_id = os.environ.get("IG_USER_ID")
    access_token = os.environ.get("IG_ACCESS_TOKEN")

    if not ig_user_id or not access_token:
        print("IG credentials (IG_USER_ID / IG_ACCESS_TOKEN) not set. Skipping Instagram post.")
        return None

    # Step 1: create a media container
    create_resp = requests.post(
        f"{GRAPH_URL}/{ig_user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
            "access_token": access_token,
        },
        timeout=60,
    )
    create_resp.raise_for_status()
    container_id = create_resp.json()["id"]

    # Step 2: poll until Instagram finishes downloading/processing the video
    waited = 0
    interval = 5
    while waited < max_wait_seconds:
        status_resp = requests.get(
            f"{GRAPH_URL}/{container_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=30,
        )
        status_resp.raise_for_status()
        status_code = status_resp.json().get("status_code")
        if status_code == "FINISHED":
            break
        if status_code == "ERROR":
            raise RuntimeError(f"Instagram failed to process the video (container {container_id})")
        time.sleep(interval)
        waited += interval
    else:
        raise TimeoutError(f"Instagram never finished processing container {container_id}")

    # Step 3: publish it
    publish_resp = requests.post(
        f"{GRAPH_URL}/{ig_user_id}/media_publish",
        data={"creation_id": container_id, "access_token": access_token},
        timeout=60,
    )
    publish_resp.raise_for_status()
    return publish_resp.json()["id"]
