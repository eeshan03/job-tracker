# Job Tracker Automation — Project Spec (Planning Phase)

Status: **Design locked. Core implementation complete — Scripts 1–3 built and tested end-to-end.**

## 1. Overview

Three independent, manually-run Python scripts that together build a personal job-opening tracker:
1. Harvest job links from Telegram channels
2. Visit those links, extract the job description, and score them against your resume
3. Monitor a fixed list of company careers pages for new postings and auto-score them

No scheduling, no daemons, no notifications — you run each script by hand, whenever you want, and review results in CSV.

## 2. File Layout

```
job-tracker/
├── config.yaml                # channels, career pages, threshold, weights, paths
├── resume.md                  # your resume
├── telegram_jobs.csv          # output of scripts 1 & 2
├── careers_jobs.csv           # output of script 3
├── state/
│   ├── telegram_state.json    # last processed message ID per channel
│   └── careers_state.json     # last seen job links per company page
├── telegram_session/          # Telethon login session (gitignored, sensitive)
├── .env                       # Telegram API ID/hash, Gemini API key (gitignored)
├── harvest_telegram.py        # Script 1
├── match_jobs.py              # Script 2
├── monitor_careers.py         # Script 3
└── common/
    ├── matcher.py             # shared: Gemini extraction + deterministic scoring
    └── scraper.py             # shared: Playwright-based page fetcher
```

## 3. Data Files

### `telegram_jobs.csv`
Written by Script 1 (bare rows), enriched by Script 2.

| Column | Filled by | Notes |
|---|---|---|
| company | Script 1 | Parsed from message text; falls back to link domain if not confidently found |
| location | Script 2 | Extracted from the JD text; blank until Script 2 processes the row |
| link | Script 1 | |
| date_added | Script 1 | |
| match | Script 1 → Script 2 | starts as `pending`, becomes `yes`/`no`/`not_a_job`/`unclear`/`error` |
| match_score | Script 2 | 0–100 |
| experience_required | Script 2 | |
| skills_required | Script 2 | |
| notes | Script 2 | one-line reason from the matcher |

### `careers_jobs.csv`
Written directly by Script 3, already fully enriched — only rows scoring ≥ threshold are added.

Same columns as above, minus the `pending` state (rows are only written once scored).

## 4. State Files

- **`telegram_state.json`** — `{channel_id: last_processed_message_id}`. Lets Script 1 catch up on exactly what's new since last run, regardless of gaps between runs.
- **`careers_state.json`** — `{company: [set of previously seen job links]}`. Lets Script 3 diff against last run to find genuinely new postings (career pages list *all* current openings, not just new ones).

## 5. Config File (`config.yaml`)

```yaml
telegram:
  channels:
    - "@channel_one"
    - "@channel_two"

careers_pages:
  - company: "Acme"
    url: "https://acme.com/careers"
  - company: "Globex"
    url: "https://globex.com/jobs"

matching:
  provider: "gemini"          # Google AI Studio / Gemini API
  skills_weight: 0.6
  experience_weight: 0.4
  careers_page_threshold: 40  # % — score needed to auto-add from careers pages
  groq_fallback_enabled: true

resume_path: "resume.md"
```

## 6. Script Behavior

### Script 1 — `harvest_telegram.py`
1. Load Telethon session (first run: one-time phone number + OTP login; reused after).
2. Read channel list from `config.yaml`.
3. Per channel, fetch messages newer than the ID in `telegram_state.json`.
4. Skip messages that fail a coarse keyword filter (`hiring`/`job`/`opening`/`apply`/etc.) — cheap first pass to cut obvious non-job chatter before any links are extracted.
5. Extract all URLs per message via regex; each URL becomes its own row.
6. Skip any link whose domain is a known non-job platform (WhatsApp, Instagram, X/Twitter, Facebook, YouTube, TikTok) — these never host real job descriptions. Ambiguous cases (e.g. `lnkd.in` short links) are *not* blocked here — left for Gemini to judge in Script 2.
7. Parse company name from surrounding message text; fall back to domain name if unclear.
8. Skip any link already present in `telegram_jobs.csv` (dedup by URL).
9. Append new rows with `match = pending`.
10. Update `telegram_state.json` with latest message ID per channel.

**Note:** the keyword and domain filters are intentionally coarse — a cheap net, not the final judge. Anything that slips through (e.g. a hackathon or newsletter link with hiring-adjacent wording) gets a real verdict from Gemini in Script 2 (see §7, `is_job_posting`).

### Script 2 — `match_jobs.py`
1. Load `resume.md` and `telegram_jobs.csv`.
2. For every row where `match == pending`:
   - Fetch the page via Playwright (handles JS-rendered career sites).
   - Extract job description text.
   - Run through `matcher.py` (see §7). If Gemini's `is_job_posting` field comes back `false` → set `match = not_a_job`, write a short note, skip scoring entirely. If no skills could be extracted from the page at all (likely incomplete content, e.g. an application-form page rather than the JD) → set `match = unclear` for manual review, also skipping scoring.
   - Otherwise, get score, match yes/no, experience, skills, location, reason and write results back into the row.
   - On failure (dead link, timeout, blocked) → log it, set `match = error`, continue — never crash the batch.
3. Small delay between requests (politeness / avoid getting blocked).

### Script 3 — `monitor_careers.py`
1. Load company list from `config.yaml`.
2. Per company, fetch careers page via Playwright, extract all current job links.
   - Link discovery makes a best-effort attempt to expand paginated/infinite-scroll listings first: clicks "load more"-style buttons a few times, then does a few scroll passes, before reading links off the page.
   - Links are filtered before further processing: kept only if they're on the same domain (or subdomain) as the careers page, *or* on a known ATS domain (Greenhouse, Lever, Workday, Ashby, etc.) — catches postings hosted on a third-party ATS instead of the company's own domain.
   - Kept links are further filtered by URL shape: must contain a job-indicating path segment (`/job/`, `/position/`, `/opening/`, etc.) or a long numeric/hex ID (how most ATS platforms encode individual postings) — cuts nav bars, footers, and social links cheaply, before any Playwright/Gemini cost is spent on them. As with the Telegram filters, this is a coarse net, not the final judge — the same `is_job_posting`/`unclear` checks from Script 2 catch anything that slips through.
3. Diff against `careers_state.json` to find new postings only.
4. For each new posting, extract JD (already fetched) → run through `matcher.py`.
5. Only append to `careers_jobs.csv` if `match_score ≥ careers_page_threshold` (currently **40%**).
6. Update `careers_state.json` with the full current set of links per company.

## 7. Matching Methodology (`matcher.py`)

Two-step process — LLM handles language understanding, script handles arithmetic (deterministic, auditable, tunable without touching prompts):

**Step 1 — Gemini extracts structured JSON** from resume + JD:
- `is_job_posting` (bool) — Gemini's final verdict on whether the page is actually a job posting at all. If `false`, the row is marked `not_a_job` and scoring is skipped (see §6, Script 2).
- `required_skills` (optionally must-have vs nice-to-have)
- `required_experience_years`
- `required_seniority`
- `location` (informational only — not scored)
- `resume_skills`
- `resume_experience_years`

**Step 2 — script computes the score:**
```
skills_score      = (matched skills ÷ total required skills) × 100
                     (must-have mismatches weighted more than nice-to-have)

experience_score  = 100 if resume_experience_years ≥ required_experience_years
                     else scaled proportionally

overall_score     = (skills_score × skills_weight) + (experience_score × experience_weight)
match             = "yes" if overall_score ≥ threshold else "no"
```
Weights and thresholds live in `config.yaml`, not hardcoded.

## 8. Key Decisions Log

| Decision | Choice | Rationale |
|---|---|---|
| Telegram access | Telethon (personal account) | Bot API can't read channels you're not admin of |
| Matching provider | Google Gemini API (free tier) | Frontier-quality, generous free daily quota, no card needed |
| Storage | Two CSVs (telegram / careers) | Simple, human-readable, sources naturally separated |
| Company name | Parsed from message text → domain fallback | Best available signal without extra scraping |
| Scraping | Playwright (headless browser) | Reliable on JS-rendered career sites |
| Notifications | None | Manual CSV review preferred |
| Execution | 3 independent manual scripts | No scheduler/orchestration needed |
| Careers-page threshold | 40% | Looser — catch more, review more |
| Score computation | LLM extraction + deterministic formula | Auditable, consistent, tunable |
| Job-relevance filtering | Two-layer: keyword + domain blocklist at harvest, Gemini `is_job_posting` as final arbiter at matching | Cuts obvious noise cheaply, but doesn't rely on a coarse filter to make the real call — avoids Playwright/API cost on obvious junk (social links, hackathons) without risking false negatives on ambiguous links |
| Careers-page link discovery | Domain relevance (same-domain/subdomain + known ATS allowlist) + URL-shape heuristic (job path keywords or numeric/hex ID) + best-effort load-more/scroll expansion | Cuts nav/footer/social noise and catches jobs hosted on third-party ATS domains, without needing per-site scraping rules; remaining noise is still caught by the Script 2 matcher's `is_job_posting`/`unclear` checks |

## 9. Tech Stack
Python · Telethon · Playwright · Google Gemini API (`google-genai`, model: `gemini-3-flash-preview`) · pandas or `csv` · PyYAML · `python-dotenv`

## 10. Deferred / Future Enhancements (not in scope yet)
- `--force` flag on Script 2 to re-evaluate rows after a resume update
- Notifications (email/desktop) — explicitly declined for now, easy to add later
- Combined entry-point script — explicitly declined, scripts stay independent
