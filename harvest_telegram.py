"""
Script 1: Harvest job links from Telegram channels you've joined.
Reads only new messages since the last run (tracked per-channel in
state/telegram_state.json) and appends new links to telegram_jobs.csv.
"""

import os
import re
import json
import csv
from pathlib import Path
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv
from telethon.sync import TelegramClient
from telethon.tl.types import MessageEntityTextUrl
from datetime import datetime, timedelta, timezone

load_dotenv()

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]

STATE_PATH = Path("state/telegram_state.json")
CSV_PATH = Path("telegram_jobs.csv")
SESSION_PATH = "telegram_session/session"

URL_REGEX = re.compile(r"https?://\S+")

# TEMP: first-run cap, remove after initial harvest
FIRST_RUN_CUTOFF = datetime.now(timezone.utc) - timedelta(days=30)

JOB_KEYWORDS = re.compile(
    r"\b(hiring|job|opening|opportunit|apply|position|vacan|role|recruit|career)\w*",
    re.IGNORECASE,
)

def is_job_related(text: str) -> bool:
    return bool(text) and bool(JOB_KEYWORDS.search(text))

BLOCKED_DOMAINS = {
    "whatsapp.com", "instagram.com", "x.com", "twitter.com", "linkedin.com/in/iamarunchauhan", "t.me", "linkedin.com/posts", "x.com", "luma.com", 
    "facebook.com", "youtube.com", "youtu.be", "tiktok.com",
}
BLOCKED_URLS = {
    "https://linkedin.com/in/iamarunchauhan",
    "https://linkedin.com/posts",
}


def is_blocked_domain(link: str) -> bool:
    parsed = urlparse(link)
    netloc = parsed.netloc.lower().removeprefix("www.")
    normalized_url = f"https://{netloc}{parsed.path.rstrip('/')}"

    domain_blocked = any(
        netloc == domain or netloc.endswith("." + domain)
        for domain in BLOCKED_DOMAINS
    )

    url_blocked = normalized_url in {
        blocked.rstrip("/")
        for blocked in BLOCKED_URLS
    }

    return domain_blocked or url_blocked


CSV_COLUMNS = [
    "company", "location", "link", "date_added", "match",
    "match_score", "experience_required", "skills_required", "notes",
]


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    STATE_PATH.parent.mkdir(exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def load_existing_links():
    if not CSV_PATH.exists():
        return set()
    with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return {row["link"] for row in reader}


def ensure_csv_exists():
    if not CSV_PATH.exists():
        with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()


def append_rows(rows):
    with open(CSV_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        for row in rows:
            writer.writerow(row)


def extract_links(message) -> list[str]:
    links = set()

    # Links visible as plain text
    if message.message:
        links.update(URL_REGEX.findall(message.message))

    # Links hidden behind hyperlinked text (e.g. "Apply here")
    if message.entities:
        for entity in message.entities:
            if isinstance(entity, MessageEntityTextUrl):
                links.add(entity.url)

    return list(links)


def guess_company_name(text: str, link: str) -> str:
    if not text:
        return urlparse(link).netloc

    match = re.search(r"company\s*[:\-]\s*(.+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip().split("\n")[0][:60]

    match = re.search(r"^([A-Z][A-Za-z0-9&.,\- ]{1,40}?)\s+is hiring", text)
    if match:
        return match.group(1).strip()

    return urlparse(link).netloc


def harvest():
    config = load_config()
    channels = config["telegram"]["channels"]

    state = load_state()
    existing_links = load_existing_links()
    ensure_csv_exists()

    new_rows = []

    with TelegramClient(SESSION_PATH, API_ID, API_HASH).start() as client:
        for channel in channels:
            last_id = state.get(channel, 0)
            max_id_seen = last_id

            for message in client.iter_messages(channel, min_id=last_id, reverse=True): #, offset_date=FIRST_RUN_CUTOFF,):
                max_id_seen = max(max_id_seen, message.id)

                if not is_job_related(message.message):
                    continue

                for link in extract_links(message):
                    if link in existing_links:
                        continue
                    if is_blocked_domain(link):
                        continue
                    company = guess_company_name(message.message, link)
                    new_rows.append({
                        "company": company,
                        "location": "",
                        "link": link,
                        "date_added": message.date.strftime("%Y-%m-%d"),
                        "match": "pending",
                        "match_score": "",
                        "experience_required": "",
                        "skills_required": "",
                        "notes": "",
                    })
                    existing_links.add(link)

            state[channel] = max_id_seen

    if new_rows:
        append_rows(new_rows)

    save_state(state)
    print(f"Added {len(new_rows)} new job link(s) to {CSV_PATH}")


if __name__ == "__main__":
    harvest()