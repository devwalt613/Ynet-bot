import os
import sys
import time
import json
import logging
import requests
import feedparser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,  # keep INFO logs out of stderr so Railway doesn't tag them as "error"
)
log = logging.getLogger("ynet-groupme")

RSS_URL = os.environ.get("RSS_URL", "https://www.ynet.co.il/Integration/StoryRss1854.xml")
GROUPME_BOT_ID = os.environ["GROUPME_BOT_ID"]          # required
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
STATE_FILE = os.environ.get("STATE_FILE", "/data/seen.json")  # mount a Railway volume at /data
MAX_BACKFILL = int(os.environ.get("MAX_BACKFILL", "5"))  # cap on first run so it doesn't dump the whole feed

GROUPME_POST_URL = "https://api.groupme.com/v3/bots/post"


def load_seen():
    try:
        with open(STATE_FILE, "r") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    # keep the set from growing forever; RSS feeds are short so trim to last 500
    trimmed = list(seen)[-500:]
    with open(STATE_FILE, "w") as f:
        json.dump(trimmed, f)


def post_to_groupme(text):
    # GroupMe caps message length around 1000 chars; trim just in case
    if len(text) > 950:
        text = text[:947] + "..."
    resp = requests.post(GROUPME_POST_URL, json={"bot_id": GROUPME_BOT_ID, "text": text}, timeout=15)
    if resp.status_code >= 300:
        log.error("GroupMe post failed (%s): %s", resp.status_code, resp.text)
    else:
        log.info("Posted: %s", text[:80])


def entry_key(entry):
    # prefer guid, fall back to link, fall back to title+published
    return entry.get("id") or entry.get("link") or (entry.get("title", "") + entry.get("published", ""))


def format_message(entry):
    title = entry.get("title", "").strip()
    # RSS <description> maps to entry.summary in feedparser; this is the fuller
    # blurb/expanded text Ynet includes, separate from the bare headline.
    summary = entry.get("summary", "").strip()
    if summary and summary != title:
        return f"{title}\n\n{summary}"
    return title


def poll_once(seen, first_run):
    feed = feedparser.parse(RSS_URL)
    if feed.bozo and not feed.entries:
        log.warning("Feed parse issue: %s", feed.bozo_exception)
        return seen

    entries = list(feed.entries)
    entries.reverse()  # oldest first, so GroupMe posts arrive in chronological order

    new_entries = [e for e in entries if entry_key(e) not in seen]

    # Always cap how many go out in a single cycle — protects against floods
    # from a stale/partial seen.json, not just a genuine first run.
    if first_run or len(new_entries) > MAX_BACKFILL:
        new_entries = new_entries[-MAX_BACKFILL:]

    for entry in new_entries:
        post_to_groupme(format_message(entry))
        seen.add(entry_key(entry))
        time.sleep(1)  # be gentle with GroupMe's rate limits

    if new_entries:
        save_seen(seen)

    return seen


def main():
    log.info("Starting Ynet -> GroupMe bot. Feed: %s Poll interval: %ss", RSS_URL, POLL_SECONDS)
    seen = load_seen()
    first_run = len(seen) == 0
    while True:
        try:
            seen = poll_once(seen, first_run)
            first_run = False
        except Exception:
            log.exception("Error during poll cycle")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
