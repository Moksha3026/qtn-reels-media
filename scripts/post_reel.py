"""
Runs inside GitHub Actions. Checks incoming/ for a video, posts the oldest one
to Instagram with a rotating caption/hashtag/comment, then moves it to posted/.

No Claude/Anthropic calls here on purpose -- captions come from CAPTION_BANK below
so this script has zero dependency on anything but GitHub + Instagram's APIs.
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

IG_USER_ID = os.environ["IG_USER_ID"]
IG_ACCESS_TOKEN = os.environ["IG_ACCESS_TOKEN"]
GRAPH_API = "https://graph.instagram.com/v21.0"

CAPTION_BANK = [
    {
        "caption": (
            "You don't need more motivation. You need fewer excuses. \U0001F525\n\n"
            "Save this for the day you want to quit.\n\n"
            "Follow @quietthenoisetoday for daily reminders to keep going.\n\n"
            "#motivation #selfimprovement #mindset #discipline #growthmindset "
            "#dailymotivation #successmindset #motivationalquotes #selfgrowth "
            "#mentalstrength #stoicism #hustle #innerpeace #consistency #levelup"
        ),
        "comment": "Which line hit you the hardest? \U0001F447 Tag someone who needs to see this.",
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
        "comment": "What's one thing discipline has taught you? \U0001F447",
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
        "comment": "Tag someone who needs to hear this today. \U0001F447",
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
        "comment": "What's the last uncomfortable thing you did on purpose? \U0001F447",
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
        "comment": "What would your future self thank you for doing today? \U0001F447",
    },
]


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"next_caption_index": 0}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def next_caption_and_comment() -> tuple[str, str]:
    state = load_state()
    idx = state.get("next_caption_index", 0) % len(CAPTION_BANK)
    entry = CAPTION_BANK[idx]
    state["next_caption_index"] = (idx + 1) % len(CAPTION_BANK)
    save_state(state)
    return entry["caption"], entry["comment"]


def find_video_to_post() -> str | None:
    if not os.path.isdir(INCOMING_DIR):
        return None
    candidates = sorted(
        f for f in os.listdir(INCOMING_DIR)
        if f.lower().endswith((".mp4", ".mov", ".m4v"))
    )
    return candidates[0] if candidates else None


def create_media_container(video_url: str, caption: str) -> str:
    resp = requests.post(
        f"{GRAPH_API}/{IG_USER_ID}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": IG_ACCESS_TOKEN,
        },
    )
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
            raise RuntimeError(f"Instagram failed to process the video: {data}")
        time.sleep(10)
    raise TimeoutError("Timed out waiting for Instagram to finish processing the video")


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
    filename = find_video_to_post()
    if not filename:
        print("No video waiting in incoming/. Nothing to do.")
        return

    raw_url = (
        f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}/"
        f"{INCOMING_DIR}/{urllib.parse.quote(filename)}"
    )
    print(f"Found {filename} -> {raw_url}")

    caption, comment = next_caption_and_comment()

    print("Creating Instagram media container...")
    creation_id = create_media_container(raw_url, caption)

    print("Waiting for Instagram to process the video...")
    wait_until_ready(creation_id)

    print("Publishing...")
    media_id = publish_container(creation_id)
    print(f"Published. Media ID: {media_id}")

    print("Posting comment...")
    post_comment(media_id, comment)

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
