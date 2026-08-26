"""
Runs inside GitHub Actions. Checks incoming/ for the oldest media file
(image OR video), posts it to Instagram, then archives it to posted/.

Caption + hashtags for each file are looked up by filename from
scripts/manifest.json (built for the 1000-image quote-card batch). Any file
with no manifest entry -- e.g. a video dropped in by hand -- falls back to
the small built-in CAPTION_BANK, so the original hand-placed-video workflow
still works exactly as before.

No Claude/Anthropic calls here on purpose -- this script has zero runtime
dependency on anything but GitHub + Instagram's APIs, so it keeps working
on schedule with nobody watching.
"""

import json
import os
import shutil
import sys
import time
import urllib.parse

import requests

REPO_OWNER = "Moksha3026"
REPO_NAME = "qtn-reels-media"
BRANCH = "main"
INCOMING_DIR = "incoming"
POSTED_DIR = "posted"
STATE_FILE = "scripts/state.json"
MANIFEST_FILE = "scripts/manifest.json"

IG_USER_ID = os.environ["IG_USER_ID"]
IG_ACCESS_TOKEN = os.environ["IG_ACCESS_TOKEN"]
GRAPH_API = "https://graph.instagram.com/v21.0"

IMAGE_EXTS = (".jpg", ".jpeg", ".png")
VIDEO_EXTS = (".mp4", ".mov", ".m4v")

# Fallback only -- used when a file in incoming/ has no matching entry in
# manifest.json (keeps the original hand-placed-video flow working).
FALLBACK_CAPTION_BANK = [
    {
        "caption": (
            "You don't need more motivation. You need fewer excuses. \U0001F525\n\n"
            "Save this for the day you want to quit.\n\n"
            "Follow @quietthenoisetoday for daily reminders to keep going.\n\n"
            "#motivation #selfimprovement #mindset #discipline #growthmindset "
            "#dailymotivation #successmindset #motivationalquotes #selfgrowth "
            "#mentalstrength #stoicism #hustle #innerpeace #consistency #levelup"
        ),
    },
    {
        "caption": (
            "Discipline isn't punishment. It's how you become the person you keep "
            "promising to be. \U0001F5A4\n\n"
            "Watch this when motivation runs out.\n\n"
            "Follow @quietthenoisetoday for more.\n\n"
            "#discipline #mindsetshift #selfmastery #mentaltoughness #dailygrind "
            "#growthmindset #buildyourself #consistencyiskey #selfdiscipline "
            "#motivationdaily #hardwork #focusup #bebetter #keepgoing"
        ),
    },
    {
        "caption": (
            "Nobody is coming to save you. That's not sad -- that's freeing. \U0001F9E0\n\n"
            "You get to build this yourself.\n\n"
            "Follow @quietthenoisetoday for daily reminders.\n\n"
            "#mindset #selfreliance #mentalhealth #innerstrength #growthmindset "
            "#motivation #selfworth #ownyourlife #dailyreminder #resilience "
            "#stoicmindset #levelup #selfimprovement #keepgoing"
        ),
    },
    {
        "caption": (
            "Comfort is expensive. You pay for it with the life you could've had. ⚡\n\n"
            "Get uncomfortable today.\n\n"
            "Follow @quietthenoisetoday for more.\n\n"
            "#comfortzone #mindset #growth #motivation #selfdiscipline #hustlemindset "
            "#dailymotivation #successhabits #mentalstrength #nopainnogain "
            "#levelup #dedication #focus #keepgoing"
        ),
    },
    {
        "caption": (
            "Your future self is watching you right now through your memories. \U0001F440\n\n"
            "Make them proud.\n\n"
            "Follow @quietthenoisetoday for daily motivation.\n\n"
            "#futureself #motivation #mindset #selfimprovement #dailygrind "
            "#growthmindset #successmindset #discipline #mentalhealth "
            "#keepgoing #hardwork #focus #stoicism #levelup"
        ),
    },
]

COMMENT_BANK = [
    "Which line hit you the hardest? \U0001F447",
    "Tag someone who needs to see this today.",
    "What's one thing you're taking from this? \U0001F447",
    "Read that again if you needed it.",
    "Save this for the day you want to quit.",
]


def load_json(path: str, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_state() -> dict:
    return load_json(STATE_FILE, {"next_fallback_index": 0, "next_comment_index": 0, "posts_made": 0})


def load_manifest() -> dict:
    return load_json(MANIFEST_FILE, {})


def find_media_to_post() -> str | None:
    if not os.path.isdir(INCOMING_DIR):
        return None
    candidates = sorted(
        f for f in os.listdir(INCOMING_DIR)
        if f.lower().endswith(IMAGE_EXTS + VIDEO_EXTS)
    )
    return candidates[0] if candidates else None


def caption_for(filename: str, state: dict, manifest: dict) -> str:
    entry = manifest.get(filename)
    if entry:
        caption = entry["caption"]
        if entry.get("hashtags"):
            caption = f"{caption}\n\n{entry['hashtags']}"
        return caption
    idx = state.get("next_fallback_index", 0) % len(FALLBACK_CAPTION_BANK)
    state["next_fallback_index"] = (idx + 1) % len(FALLBACK_CAPTION_BANK)
    return FALLBACK_CAPTION_BANK[idx]["caption"]


def comment_for(state: dict) -> str:
    idx = state.get("next_comment_index", 0) % len(COMMENT_BANK)
    state["next_comment_index"] = (idx + 1) % len(COMMENT_BANK)
    return COMMENT_BANK[idx]


def create_media_container(media_url: str, caption: str, is_video: bool) -> str:
    data = {"caption": caption, "access_token": IG_ACCESS_TOKEN}
    if is_video:
        data["media_type"] = "REELS"
        data["video_url"] = media_url
    else:
        data["image_url"] = media_url
    resp = requests.post(f"{GRAPH_API}/{IG_USER_ID}/media", data=data)
    resp.raise_for_status()
    return resp.json()["id"]


def wait_until_ready(creation_id: str, timeout_seconds: int = 300) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        resp = requests.get(
            f"{GRAPH_API}/{creation_id}",
            params={"fields": "status_code,status", "access_token": IG_ACCESS_TOKEN},
        )
        resp.raise_for_status()
        data = resp.json()
        status_code = data.get("status_code")
        if status_code == "FINISHED":
            return
        if status_code == "ERROR":
            raise RuntimeError(f"Instagram failed to process the media: {data}")
        time.sleep(10)
    raise TimeoutError("Timed out waiting for Instagram to finish processing the media")


def publish_container(creation_id: str) -> str:
    resp = requests.post(
        f"{GRAPH_API}/{IG_USER_ID}/media_publish",
        data={"creation_id": creation_id, "access_token": IG_ACCESS_TOKEN},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def post_comment(media_id: str, message: str) -> None:
    requests.post(
        f"{GRAPH_API}/{media_id}/comments",
        data={"message": message, "access_token": IG_ACCESS_TOKEN},
    ).raise_for_status()


def main() -> None:
    filename = find_media_to_post()
    if not filename:
        print("No media waiting in incoming/. Nothing to do.")
        return

    is_video = filename.lower().endswith(VIDEO_EXTS)
    raw_url = (
        f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}/"
        f"{INCOMING_DIR}/{urllib.parse.quote(filename)}"
    )
    print(f"Found {filename} ({'video' if is_video else 'image'}) -> {raw_url}")

    state = load_state()
    manifest = load_manifest()
    caption = caption_for(filename, state, manifest)
    comment = comment_for(state)

    print("Creating Instagram media container...")
    creation_id = create_media_container(raw_url, caption, is_video)

    print("Waiting for Instagram to process the media...")
    wait_until_ready(creation_id, timeout_seconds=300 if is_video else 60)

    print("Publishing...")
    media_id = publish_container(creation_id)
    print(f"Published. Media ID: {media_id}")

    print("Posting comment...")
    try:
        post_comment(media_id, comment)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: comment failed (non-fatal): {exc}")

    state["posts_made"] = state.get("posts_made", 0) + 1
    save_json(STATE_FILE, state)

    os.makedirs(POSTED_DIR, exist_ok=True)
    shutil.move(
        os.path.join(INCOMING_DIR, filename),
        os.path.join(POSTED_DIR, filename),
    )
    print(f"Moved {filename} to {POSTED_DIR}/")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
