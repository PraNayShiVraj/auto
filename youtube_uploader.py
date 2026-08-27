"""
YouTube Data API v3 upload helper. Uses the same OAuth refresh token as
drive_utils.py (one Desktop OAuth Client, one login, one refresh token
covering both Drive and YouTube scopes — see get_google_token.py).
"""
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_youtube_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_video(file_path, title, description="", tags=None, privacy_status="public", made_for_kids=False):
    """Uploads a video and returns its YouTube video id."""
    service = get_youtube_service()
    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags or [],
            "categoryId": "24",  # Entertainment
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }
    media = MediaFileUpload(file_path, chunksize=-1, resumable=True, mimetype="video/*")
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"YouTube upload progress: {int(status.progress() * 100)}%")

    return response["id"]


def update_video_description(video_id: str, extra_text: str):
    """Appends extra_text (e.g. next part link) to an existing YouTube video's description."""
    try:
        service = get_youtube_service()
        resp = service.videos().list(part="snippet,status", id=video_id).execute()
        items = resp.get("items", [])
        if not items:
            return
        video_data = items[0]
        snippet = video_data["snippet"]
        current_desc = snippet.get("description", "")

        if extra_text in current_desc:
            return

        new_desc = f"{current_desc.strip()}\n\n{extra_text}".strip()
        body = {
            "id": video_id,
            "snippet": {
                "title": snippet["title"],
                "description": new_desc,
                "categoryId": "24",  # Entertainment
                "tags": snippet.get("tags", []),
            },
            "status": video_data.get("status", {}),
        }
        service.videos().update(part="snippet,status", body=body).execute()
        print(f"Updated YouTube video {video_id} description with link: {extra_text}")
    except Exception as e:
        import sys
        print(f"Failed to update YouTube video {video_id} description: {e}", file=sys.stderr)
