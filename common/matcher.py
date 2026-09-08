"""
Shared Gemini-based extraction + deterministic scoring, used by
match_jobs.py and monitor_careers.py.
"""

import os
import time
import yaml
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

MODEL_NAME = "gemini-3-flash-preview"  # free tier

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

GROQ_MODEL_NAME = "openai/gpt-oss-20b"
_groq_client = None
def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        _groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _groq_client


class ExtractedFields(BaseModel):
    is_job_posting: bool
    required_skills: list[str]
    required_experience_years: float
    experience_estimated: bool
    required_seniority: str
    location: str
    resume_skills: list[str]
    resume_experience_years: float


EXTRACTION_PROMPT = """You are extracting structured fields from a resume and a job description.

From the JOB DESCRIPTION, identify:
- is_job_posting: true if this page is an actual job posting/description; false if it's not a job posting at all (e.g. a hackathon page, generic article, or unrelated content). If false, fill the other fields with reasonable placeholders — they won't be used.
- required_skills: technical skills/technologies this role asks for
- required_experience_years: minimum years required (numeric; use lower bound of a range; estimate from seniority if not stated)
- experience_estimated: true if required_experience_years was NOT explicitly stated as a number in the JD and you had to estimate/infer it (e.g. from seniority level or vague wording); false if a clear numeric value was given
- required_seniority: junior, mid, senior, or lead
- location: the job's location as stated (city/country, "Remote", or "Hybrid - <city>"); use "Not specified" if not mentioned anywhere

From the RESUME, identify:
- resume_skills: the flat list of skills in the Skills section
- resume_experience_years: the number from the "Total Experience" field

RESUME:
{resume}

JOB DESCRIPTION:
{jd}
"""


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)



def extract_fields(resume_text: str, jd_text: str, max_retries: int = 3, groq_fallback_enabled: bool = False) -> ExtractedFields:
    prompt = EXTRACTION_PROMPT.format(resume=resume_text, jd=jd_text)
    last_error = None
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ExtractedFields,
                ),
            )
            return ExtractedFields.model_validate_json(response.text)
        except Exception as e:
            last_error = e
            if "RESOURCE_EXHAUSTED" in str(e):
                print(f"Gemini quota exhausted ({e}); skipping retries, falling back to Groq...")
                break
            wait = 15 * (attempt + 1)
            print(f"Gemini call failed ({e}); retrying in {wait}s...")
            time.sleep(wait)

    if groq_fallback_enabled:
        print("Gemini retries exhausted; falling back to Groq...")
        try:
            return _extract_fields_groq(resume_text, jd_text)
        except Exception as groq_error:
            print(f"Groq fallback also failed ({groq_error})")
            last_error = groq_error
            if "Rate limit reached" in str(groq_error):
                raise SystemExit("Groq rate limit reached; stopping execution.") from groq_error

    raise last_error


def _extract_fields_groq(resume_text: str, jd_text: str) -> ExtractedFields:
    prompt = EXTRACTION_PROMPT.format(resume=resume_text, jd=jd_text)
    schema = ExtractedFields.model_json_schema()
    schema["additionalProperties"] = False

    groq_client = _get_groq_client()
    response = groq_client.chat.completions.create(
        model=GROQ_MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "ExtractedFields",
                "strict": True,
                "schema": schema,
            },
        },
    )
    return ExtractedFields.model_validate_json(response.choices[0].message.content)


def compute_score(fields: ExtractedFields, skills_weight: float, experience_weight: float) -> dict:
    required = {s.strip().lower() for s in fields.required_skills}
    have = {s.strip().lower() for s in fields.resume_skills}
    matched = required & have

    skills_score = (len(matched) / len(required) * 100) if required else 50

    if (fields.resume_experience_years + 1) >= fields.required_experience_years:
        experience_score = 100
    elif fields.required_experience_years > 0:
        experience_score = (fields.resume_experience_years / fields.required_experience_years) * 100
    else:
        experience_score = 100

    overall_score = round((skills_score * skills_weight) + (experience_score * experience_weight), 1)

    return {
        "match_score": overall_score,
        "matched_skills": sorted(matched),
        "missing_skills": sorted(required - have),
    }


def match_resume_to_job(resume_text: str, jd_text: str, config: dict) -> dict:
    weights = config["matching"]
    groq_fallback_enabled = weights.get("groq_fallback_enabled", False)
    fields = extract_fields(resume_text, jd_text, groq_fallback_enabled=groq_fallback_enabled)

    if not fields.is_job_posting:
        return {
            "match": "not_a_job",
            "match_score": "",
            "experience_required": "",
            "skills_required": "",
            "location": "",
            "notes": "Filtered by AI: not a job posting",
        }

    if not fields.required_skills:
        return {
            "match": "unclear",
            "match_score": "",
            "experience_required": "",
            "skills_required": "",
            "location": fields.location,
            "notes": "No skills detected in the page content — likely an incomplete page (e.g. an application form rather than the full JD). Review manually.",
        }

    scoring = compute_score(fields, weights["skills_weight"], weights["experience_weight"])
    threshold = weights.get("careers_page_threshold", 40)
    match_flag = "yes" if scoring["match_score"] >= threshold else "no"

    reason = (
        f"{len(scoring['matched_skills'])}/{len(fields.required_skills)} skills matched; "
        f"{fields.resume_experience_years}y experience vs {fields.required_experience_years}y required."
    )
    if fields.experience_estimated:
        reason += " (experience requirement estimated, not explicitly stated)"

    return {
        "match": match_flag,
        "match_score": scoring["match_score"],
        "experience_required": f"{fields.required_experience_years} years ({fields.required_seniority})",
        "skills_required": ", ".join(fields.required_skills),
        "location": fields.location,
        "notes": reason,
    }


if __name__ == "__main__":
    config = load_config()
    with open(config["resume_path"], "r", encoding="utf-8") as f:
        resume_text = f.read()

    sample_jd = """
    We are looking for a Software Engineer with 3+ years of experience in Python,
    Django, AWS, and Docker. Familiarity with REST APIs is required.
    """

    result = match_resume_to_job(resume_text, sample_jd, config)
    print(result)