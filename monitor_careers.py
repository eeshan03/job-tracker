"""
Script 3: Check company careers pages for new postings, score them against
your resume, and auto-add the ones that clear the threshold to careers_jobs.csv.
"""

import csv
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import yaml
import re

from common.scraper import fetch_page_links, fetch_page_text
from common.matcher import match_resume_to_job

STATE_PATH = Path("state/careers_state.json")
CSV_PATH = Path("careers_jobs.csv")
CSV_COLUMNS = [
    "company", "location", "link", "date_added", "match",
    "match_score", "experience_required", "skills_required", "notes",
]

DELAY_SECONDS = 7  # same Gemini free-tier pacing as match_jobs.py

KNOWN_ATS_DOMAINS = {
    "greenhouse.io", "lever.co", "myworkdayjobs.com", "ashbyhq.com",
    "smartrecruiters.com", "icims.com", "workable.com", "jobvite.com",
    "breezy.hr", "bamboohr.com", "recruitee.com", "personio.com",
}

NOISE_PATH_KEYWORDS = re.compile(
    r"/(about|blog|news|contact|login|signin|signup|privacy|terms|cookie|faq|help|support|"
    r"benefits|culture|diversity|life-at|team|leadership|investors|press|media|sitemap|accessibility)"
    r"(/|$)",
    re.IGNORECASE,
)

JOB_PATH_KEYWORDS = re.compile(
    r"/(job|jobs|position|positions|opening|openings|vacan|career|req|posting)s?([/-]|$)",
    re.IGNORECASE,
)

SOCIAL_DOMAINS = {
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "linkedin.com", "youtube.com", "tiktok.com",
}

def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_resume(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_state() -> dict:
    if STATE_PATH.exists():
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    STATE_PATH.parent.mkdir(exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def ensure_csv_exists():
    if not CSV_PATH.exists():
        with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=CSV_COLUMNS).writeheader()


def append_row(row: dict):
    with open(CSV_PATH, "a", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=CSV_COLUMNS).writerow(row)


def is_relevant_domain(link: str, base_url: str) -> bool:
    link_domain = urlparse(link).netloc.lower()
    base_domain = urlparse(base_url).netloc.lower()
    if link_domain == base_domain or link_domain.endswith("." + base_domain):
        return True
    return any(link_domain == d or link_domain.endswith("." + d) for d in KNOWN_ATS_DOMAINS)

def looks_like_job_link(link: str) -> bool:
    parsed = urlparse(link)
    if parsed.scheme not in ("http", "https"):
        return False
    domain = parsed.netloc.lower().removeprefix("www.")
    if any(domain == d or domain.endswith("." + d) for d in SOCIAL_DOMAINS):
        return False
    if NOISE_PATH_KEYWORDS.search(parsed.path):
        return False
    if JOB_PATH_KEYWORDS.search(parsed.path):
        return True
    # Fallback: most ATS job URLs embed a long numeric or hex/UUID-style ID
    return bool(re.search(r"[0-9a-fA-F]{6,}|\d{4,}", parsed.path))


def run():
    config = load_config()
    resume_text = load_resume(config["resume_path"])
    state = load_state()
    ensure_csv_exists()

    for entry in config["careers_pages"]:
        company, url = entry["company"], entry["url"]
        print(f"Checking {company}...")

        try:
            links = fetch_page_links(url)
        except Exception as e:
            print(f"  Failed to fetch careers page: {e}")
            continue

        job_links = {
            l for l in links
            if is_relevant_domain(l, url) and looks_like_job_link(l)
        }
        previously_seen = set(state.get(company, []))
        new_links = job_links - previously_seen
        print(f"  {len(job_links)} total links, {len(new_links)} new.")
        print("  List of links:")
        for link in sorted(new_links):
            print(f"    - {link}")

        successfully_processed = set()

        for link in new_links:
            print(f"  -> {link}")
            try:
                jd_text = fetch_page_text(link)
                if not jd_text or len(jd_text.strip()) < 50:
                    print("     Skipped: no readable content")
                    successfully_processed.add(link)
                    time.sleep(DELAY_SECONDS)
                    continue

                result = match_resume_to_job(resume_text, jd_text, config)

                if result["match"] == "yes":
                    append_row({
                        "company": company,
                        "location": result["location"],
                        "link": link,
                        "date_added": time.strftime("%Y-%m-%d"),
                        "match": result["match"],
                        "match_score": result["match_score"],
                        "experience_required": result["experience_required"],
                        "skills_required": result["skills_required"],
                        "notes": result["notes"],
                    })
                    print(f"     Added (score {result['match_score']})")
                else:
                    print(f"     Not added (match={result['match']})")

                successfully_processed.add(link)

            except Exception as e:
                print(f"     Error, will retry next run: {e}")
                # NOT added to successfully_processed -> stays "new" next run

            time.sleep(DELAY_SECONDS)

        state[company] = list(previously_seen | successfully_processed)

    save_state(state)
    print("Done.")


if __name__ == "__main__":
    run()