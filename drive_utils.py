"""
Helpers for talking to Google Drive using your own Google account via
OAuth (the same Desktop Client / refresh token used for YouTube — see
get_google_token.py). No service account, no folder-sharing needed:
it's your own Drive, accessed as you.
"""
import io
import json
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]
VIDEO_MIME_PREFIX = "video/"


def get_drive_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_videos(service, folder_id):
    """Returns all video files in the folder, sorted by name (001_x.mp4, 002_y.mp4, ...).

    Sorting by filename means YOU control the posting order just by how
    you name the files in Drive. Prefix them 01_, 02_, 03_... if you
    want a specific sequence.
    """
    files = []
    page_token = None
    query = f"'{folder_id}' in parents and trashed = false and mimeType contains 'video/'"
    while True:
        resp = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, createdTime, mimeType)",
                orderBy="name",
                pageToken=page_token,
                pageSize=100,
            )
            .execute()
        )
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def download_file(service, file_id, dest_path):
    request = service.files().get_media(fileId=file_id)
    with io.FileIO(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest_path


def delete_file(service, file_id):
    """Permanently deletes a file from Drive (skips the Trash)."""
    service.files().delete(fileId=file_id).execute()


def get_file_stream(service, file_id):
    """Returns a file-like object streaming the video bytes (used by server.py)."""
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return buf


# ---- state.json persistence (stored as a file inside the same Drive folder) ----

STATE_FILENAME = "autopost_state.json"


def _find_state_file(service, folder_id):
    resp = (
        service.files()
        .list(
            q=f"'{folder_id}' in parents and trashed = false and name = '{STATE_FILENAME}'",
            fields="files(id, name)",
        )
        .execute()
    )
    items = resp.get("files", [])
    return items[0]["id"] if items else None


def load_state(service, folder_id):
    file_id = _find_state_file(service, folder_id)
    if not file_id:
        return {}
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    try:
        return json.loads(buf.read().decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(service, folder_id, state: dict):
    file_id = _find_state_file(service, folder_id)
    data = json.dumps(state, indent=2).encode("utf-8")
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/json", resumable=False)
    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        metadata = {"name": STATE_FILENAME, "parents": [folder_id]}
        service.files().create(body=metadata, media_body=media, fields="id").execute()
