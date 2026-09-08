"""
Script 2: Visit each pending link in telegram_jobs.csv, extract the job
description, and score it against your resume using matcher.py.

Usage:
    python match_jobs.py        # process all pending rows
    python match_jobs.py 5      # process only the first 5 pending rows (for testing)
"""

import csv
import sys
import time
from pathlib import Path

import yaml

from common.scraper import fetch_page_text
from common.matcher import match_resume_to_job

CSV_PATH = Path("telegram_jobs.csv")
CSV_COLUMNS = [
    "company", "location", "link", "date_added", "match",
    "match_score", "experience_required", "skills_required", "notes",
]

DELAY_SECONDS = 7  # keeps us under Gemini free tier's 10 requests/minute


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_resume(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_rows():
    with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def save_rows(rows):
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def process_row(row: dict, resume_text: str, config: dict) -> dict:
    link = row["link"]
    try:
        jd_text = fetch_page_text(link)
    except Exception as e:
        row["match"] = "error"
        row["notes"] = f"Scraper fetch failed: {e}"
        return row

    if not jd_text or len(jd_text.strip()) < 50:
        row["match"] = "error"
        row["notes"] = "Fetched page had no readable content"
        return row

    try:
        result = match_resume_to_job(resume_text, jd_text, config)
    except Exception as e:
        row["match"] = "error"
        row["notes"] = f"Matching failed: {e}"
        return row

    row.update(result)
    return row


def run(limit=None):
    config = load_config()
    resume_text = load_resume(config["resume_path"])
    rows = load_rows()

    pending_indices = [i for i, r in enumerate(rows) if r["match"] == "pending"]
    if limit:
        pending_indices = pending_indices[:limit]

    print(f"Processing {len(pending_indices)} pending row(s).")

    for count, i in enumerate(pending_indices, 1):
        row = rows[i]
        print(f"[{count}/{len(pending_indices)}] {row['link']}")
        rows[i] = process_row(row, resume_text, config)
        save_rows(rows)  # persist after every row so a crash doesn't lose progress
        time.sleep(DELAY_SECONDS)

    print("Done.")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run(limit)