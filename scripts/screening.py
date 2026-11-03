#!/usr/bin/env python3
"""Screening helpers for fetch_telegram_jobs.py: citizenship / work-authorization screening of a post
via the LLM (backend.call_llm), a regex hard-exclusion list, the screening cache, and html_to_text.
"""
import hashlib
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, load_config  # noqa: E402
from backend import call_llm  # noqa: E402

TAG_RE = re.compile(r"<[^>]+>")
PROFILE_FILE = ROOT / "profile.md"
SCREEN_CACHE_FILE = ROOT / "screened_jobs.json"
DESCRIPTION_CHARS = 4000
JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}")
SCREEN_SYSTEM_PROMPT = "You classify job postings. Reply with only the requested single-line JSON object."
WORK_AUTH_RE = re.compile(r"^\s*Work Authorization:\s*(.+)$", re.I | re.M)

# Unambiguous disqualifiers for this candidate — excluded without spending an LLM call.
HARD_EXCLUDE_RE = re.compile(
    r"\b(must be an? (u\.?s\.?|united states) citizen"
    r"|(u\.?s\.?|united states) citizens?(hip)?( is)? (required|only)"
    r"|ts/sci|top secret|secret clearance|active (security )?clearance|security clearance( is)? required"
    r"|w-?2 only)\b",
    re.I,
)

SCREEN_PROMPT_TEMPLATE = """You are screening a single job posting for whether its citizenship/
work-authorization requirement is compatible with this candidate.

Candidate's work authorization (verbatim from their profile):
{work_authorization}

Job posting:
Title: {title}
Company: {company}
Location: {location}
Description:
{description}

IMPORTANT: a posting that restricts eligibility to a specific list of countries/regions (e.g.
"open to applicants in Spain, Sweden, UK, Ireland, or Germany", "must be based in the EU") is a
citizenship/work-authorization restriction even if it never uses the words "citizenship" or "work
authorization" — judge it the same way you would an explicit citizenship requirement. If {home_country}
(the candidate's own country) is not in that list, this is "excluded", never "passed", regardless
of whether the posting also says "remote" — "remote" describing a role that is nonetheless scoped
to specific countries is not the same as remote-anywhere.

Decide one of:
- "passed" — the posting does not restrict eligibility to a specific country/region list that
  excludes {home_country}, and does not require a citizenship/work-authorization status the candidate
  lacks (e.g. no citizenship or location restriction mentioned at all, explicitly flexible/
  remote-anywhere/worldwide, C2C/1099/contract-friendly with no location caveat, visa sponsorship
  offered, or a country list that DOES include {home_country}).
- "excluded" — the posting explicitly requires a citizenship or work authorization the candidate's
  profile does not state they have (e.g. "must be a US citizen", "must be authorized to work in
  the EU", a security-clearance role requiring citizenship the candidate doesn't hold), OR it
  restricts eligibility to a country/region list that does not include {home_country} (see IMPORTANT
  above) — this is the more common case in practice, not an edge case.
- "ambiguous" — the posting doesn't clearly state its citizenship/work-authorization requirement
  either way, AND doesn't list any specific eligible countries/regions either.

Do not guess a genuinely unclear posting into "excluded" — use "ambiguous" instead. But a country/
region list that excludes {home_country} is NOT ambiguous — it's "excluded", per the IMPORTANT rule above.

Respond with ONLY a single-line JSON object, no other text: {{"status": "passed|excluded|ambiguous", "reason": "one short sentence"}}
"""

FALLBACK_PROMPT_TEMPLATE = """You are re-screening a {role_label} job posting that came back "ambiguous"
on a first pass — this is a fallback used only because that normal run found zero postings worth
saving. This time, give a decisive answer instead of "ambiguous".

Candidate's work authorization (verbatim from their profile):
{work_authorization}

Job posting:
Title: {title}
Company: {company}
Location: {location}
Description:
{description}

Decide one of:
- "passed" — the posting is genuinely open to being done from anywhere in the world, with no
  location/citizenship restriction: it explicitly says remote-anywhere, hires globally, or is
  C2C/1099/contract-friendly with no location caveat.
- "excluded" — anything else, including a location tag next to "Remote" that suggests a regional
  restriction (e.g. "Remote" + "Spain"), or the posting simply never saying either way. Unlike the
  first pass, silence here means "excluded," not another round of "ambiguous" — this fallback only
  wants postings that are unambiguously open to anyone, anywhere.

Respond with ONLY a single-line JSON object, no other text: {{"status": "passed|excluded", "reason": "one short sentence"}}
"""


def read_work_authorization() -> str:
    if not PROFILE_FILE.exists():
        raise SystemExit(
            f"{PROFILE_FILE} not found — run /setup in Claude Code, or copy profile.md.example to profile.md and fill in your "
            "own facts (including Work Authorization) before running the fetcher, since it now "
            "screens every posting's citizenship requirement against that field."
        )
    m = WORK_AUTH_RE.search(PROFILE_FILE.read_text(encoding="utf-8"))
    if not m or not m.group(1).strip():
        raise SystemExit(
            f'{PROFILE_FILE} has no "Work Authorization: ..." line — add one before running the '
            "fetcher, it's the only profile fact the citizenship screen uses."
        )
    return m.group(1).strip()


def ask_verdict(template: str, work_authorization: str, job: dict, allowed: tuple) -> dict:
    config = load_config()
    prompt = template.format(
        work_authorization=work_authorization,
        home_country=config["home_country"],
        role_label=config["role_label"],
        title=job["title"],
        company=job["companyName"],
        location=job["location"],
        description=job["descriptionText"][:DESCRIPTION_CHARS],
    )
    raw = call_llm(prompt, SCREEN_SYSTEM_PROMPT)
    m = JSON_OBJECT_RE.search(raw)
    if not m:
        raise ValueError(f"no JSON object in reply: {raw[:200]!r}")
    verdict = json.loads(m.group(0))
    if verdict.get("status") not in allowed:
        raise ValueError(f"unexpected status: {verdict.get('status')!r}")
    return {"status": verdict["status"], "reason": verdict.get("reason", "")}


def hard_exclusion(job: dict) -> dict | None:
    m = HARD_EXCLUDE_RE.search(f"{job['title']} {job['location']} {job['descriptionText']}")
    if not m:
        return None
    return {"status": "excluded", "reason": f"hard-exclusion pattern matched: {m.group(0)!r}"}


def screen_citizenship(work_authorization: str, job: dict) -> tuple[dict, bool]:
    """Returns (verdict, cacheable). Screening failures are not cacheable — they're transient."""
    try:
        return ask_verdict(SCREEN_PROMPT_TEMPLATE, work_authorization, job, ("passed", "excluded", "ambiguous")), True
    except Exception as e:
        return {"status": "excluded", "reason": f"citizenship screening failed, excluding conservatively: {e}"}, False


def screen_fully_remote(work_authorization: str, job: dict) -> tuple[dict, bool]:
    try:
        return ask_verdict(FALLBACK_PROMPT_TEMPLATE, work_authorization, job, ("passed", "excluded")), True
    except Exception as e:
        return {"status": "excluded", "reason": f"fallback screening failed, excluding conservatively: {e}"}, False


def posting_hash(job: dict) -> str:
    text = f"{job['title']}\n{job['location']}\n{job['descriptionText']}"
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def load_screen_cache() -> dict:
    if SCREEN_CACHE_FILE.exists():
        return json.loads(SCREEN_CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def save_screen_cache(cache: dict) -> None:
    SCREEN_CACHE_FILE.write_text(json.dumps(cache, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def html_to_text(s: str) -> str:
    return re.sub(r"\n\s*\n+", "\n\n", TAG_RE.sub("\n", html.unescape(html.unescape(s or "")))).strip()

