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
    """Appends extra_text (e.g. next/prev part links) to an existing YouTube video's description and ensures Category 24."""
    try:
        service = get_youtube_service()
        resp = service.videos().list(part="snippet,status", id=video_id).execute()
        items = resp.get("items", [])
        if not items:
            return
        video_data = items[0]
        snippet = video_data["snippet"]
        current_desc = snippet.get("description", "")

        new_lines = []
        for line in extra_text.split("\n\n"):
            line_str = line.strip()
            if line_str and line_str not in current_desc:
                new_lines.append(line_str)

        category_needed = (snippet.get("categoryId") != "24")

        if not new_lines and not category_needed:
            return

        new_desc = current_desc
        if new_lines:
            added_text = "\n\n".join(new_lines)
            new_desc = f"{current_desc.strip()}\n\n{added_text}".strip()

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
        print(f"Updated YouTube video {video_id} description/category. Extra: {extra_text}")
    except Exception as e:
        import sys
        print(f"Failed to update YouTube video {video_id}: {e}", file=sys.stderr)


def get_my_uploaded_videos():
    """Returns a list of {"id": video_id, "title": title, "description": desc} for videos on the channel."""
    try:
        service = get_youtube_service()
        channels_resp = service.channels().list(mine=True, part="contentDetails").execute()
        items = channels_resp.get("items", [])
        if not items:
            return []
        uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        videos = []
        page_token = None
        while True:
            playlist_resp = service.playlistItems().list(
                playlistId=uploads_playlist_id,
                part="snippet",
                maxResults=50,
                pageToken=page_token
            ).execute()
            for item in playlist_resp.get("items", []):
                snippet = item["snippet"]
                vid_id = snippet["resourceId"]["videoId"]
                title = snippet["title"]
                desc = snippet.get("description", "")
                videos.append({"id": vid_id, "title": title, "description": desc})
            page_token = playlist_resp.get("nextPageToken")
            if not page_token or len(videos) >= 100:
                break
        return videos
    except Exception as e:
        import sys
        print(f"Error fetching channel videos: {e}", file=sys.stderr)
        return []
