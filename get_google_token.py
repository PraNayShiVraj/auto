"""
RUN THIS ONCE, ON YOUR OWN COMPUTER (not on Render).

It opens a browser, asks you to log into your Google account (the same
one that owns both the Drive folder and the YouTube channel), and
prints ONE refresh token that works for both Drive and YouTube — since
you're using a single Desktop OAuth Client for both.

Prereqs:
  1. In Google Cloud Console, enable "Google Drive API" and
     "YouTube Data API v3" for your project.
  2. You already have an OAuth Client ID of type "Desktop" — download
     its JSON (Console → Credentials → click the client → Download JSON)
     and save it as client_secret.json next to this script.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/youtube.upload",
]


def main():
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    # access_type=offline + prompt=consent guarantee a refresh_token comes
    # back even if you've authorized this app before.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    print("\n\n=== SAVE THESE 3 VALUES IN RENDER (on every service) ===")
    print("GOOGLE_OAUTH_CLIENT_ID     =", creds.client_id)
    print("GOOGLE_OAUTH_CLIENT_SECRET =", creds.client_secret)
    print("GOOGLE_OAUTH_REFRESH_TOKEN =", creds.refresh_token)


if __name__ == "__main__":
    main()

