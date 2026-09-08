# Job Tracker Automation

A personal job-tracking tool that:
1. Harvests job links from Telegram channels you've joined
2. Visits each link, extracts the job description, and scores it against your resume using Gemini
3. Monitors company careers pages for new postings and auto-adds the ones that match

No scheduling, no daemons — three independent scripts you run manually, results land in CSV.

See [`job_tracker_spec.md`](./job_tracker_spec.md) for full design details, schemas, and rationale.

## Setup

**1. Clone and create a virtual environment**
```
git clone <repo-url>
cd job-tracker
python -m venv venv
```
Activate it:
- Windows: `.\venv\Scripts\Activate.ps1`
- macOS/Linux: `source venv/bin/activate`

**2. Install dependencies**
```
pip install telethon playwright google-genai pydantic pandas pyyaml python-dotenv
playwright install chromium
```

**3. Get API credentials**
- Telegram `api_id` / `api_hash` from https://my.telegram.org (API development tools)
- Gemini API key from https://aistudio.google.com/apikey

Create a `.env` file in the project root:
```
TELEGRAM_API_ID=your_api_id_here
TELEGRAM_API_HASH=your_api_hash_here
GEMINI_API_KEY=your_gemini_key_here
```

**4. Configure the project**
- Fill in `config.yaml` with your Telegram channels, career pages, and matching weights/threshold
- Add your resume to `resume.md` (see the spec for the recommended format)

## Usage

Run each script manually, in this order, whenever you want fresh results:

```
python harvest_telegram.py    # pulls new links from Telegram → telegram_jobs.csv
python match_jobs.py          # scores pending links against your resume
python monitor_careers.py     # checks careers pages, auto-adds matches → careers_jobs.csv
```

First run of `harvest_telegram.py` will prompt for a one-time Telegram login (phone number + code).

Results land in `telegram_jobs.csv` and `careers_jobs.csv` — open in Excel/Sheets to review.

## Project structure
```
job-tracker/
├── config.yaml
├── resume.md
├── telegram_jobs.csv
├── careers_jobs.csv
├── state/              # tracks what's already been processed
├── telegram_session/   # Telethon login (gitignored)
├── .env                 # API keys (gitignored)
├── harvest_telegram.py
├── match_jobs.py
├── monitor_careers.py
└── common/
    ├── scraper.py
    └── matcher.py
```
