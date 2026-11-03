#!/usr/bin/env python3
"""Shared primitives: search_config.json, the target-role filter, posting age, load/save, record()."""
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS_FILE = ROOT / "telegram_jobs.json"
LOG_FILE = ROOT / "telegram_log.json"
PROFILE_FILE = ROOT / "profile.md"
CONFIG_FILE = ROOT / "search_config.json"
DEFAULT_MAX_AGE_DAYS = 30
_DATE_LINE_RE = re.compile(r"^\s*(\d{2})/(\d{2})/(\d{4})\s*$", re.M)
_CONFIG_REQUIRED = ("role_label", "role_regex", "other_lang_regex", "other_stack_title_regex",
                    "home_country", "home_location_regex")
_config: dict | None = None


def load_config(required: bool = True) -> dict:
    """search_config.json: what roles to look for and where the candidate is (see
    search_config.json.example). With required=False a missing/blank file gives {}."""
    global _config
    if _config is None:
        try:
            _config = json.loads(CONFIG_FILE.read_text(encoding="utf-8") or "{}")
        except FileNotFoundError:
            _config = {}
    missing = [k for k in _CONFIG_REQUIRED if not _config.get(k)]
    if required and missing:
        raise SystemExit(f"{CONFIG_FILE} is missing {', '.join(missing)} — copy "
                         "search_config.json.example to search_config.json and fill it in.")
    return _config


def config_re(key: str) -> re.Pattern:
    return re.compile(load_config()[key], re.I)


def is_target_role(job: dict) -> bool:
    """Whether a post is for the role in search_config.json (role_regex). A title matching it
    qualifies; a title naming another stack (other_stack_title_regex) doesn't; a neutral title
    ("Backend Engineer") needs the role to be the posting's main language — matched at least as
    often as all other languages (other_lang_regex) combined, so an "or Python/Java/Node"
    alternatives list doesn't count."""
    role, other_title, other_lang = (config_re(k) for k in
                                     ("role_regex", "other_stack_title_regex", "other_lang_regex"))
    title = job.get("title") or ""
    if role.search(title):
        return True
    if other_title.search(title):
        return False
    desc = job.get("descriptionText") or ""
    hits = len(role.findall(desc))
    return hits > 0 and hits >= len(other_lang.findall(desc))


def posting_age_days(job: dict) -> int | None:
    """Days since the posting went up, or None if unknown.

    Uses "postedAt" (ISO date, set by the fetcher); leads saved before that field existed fall
    back to the MM/DD/YYYY header line that is often captured at the top of descriptionText.
    """
    posted = None
    if job.get("postedAt"):
        try:
            posted = date.fromisoformat(job["postedAt"])
        except ValueError:
            pass
    if posted is None:
        m = _DATE_LINE_RE.search((job.get("descriptionText") or "")[:1500])
        if m:
            try:
                posted = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            except ValueError:
                pass
    return (date.today() - posted).days if posted else None


def load_jobs(jobs_file: Path = JOBS_FILE) -> list:
    if not jobs_file.exists():
        return []
    return json.loads(jobs_file.read_text(encoding="utf-8"))


def save_jobs(jobs: list, jobs_file: Path = JOBS_FILE) -> None:
    jobs_file.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")


def select_job(jobs: list, job_id: str) -> dict:
    for job in jobs:
        if job.get("id") == job_id:
            return job
    raise SystemExit(f"No job found with id={job_id}")


def load_log(log_file: Path = LOG_FILE) -> dict:
    if log_file.exists():
        return json.loads(log_file.read_text(encoding="utf-8"))
    return {}


def save_log(log: dict, log_file: Path = LOG_FILE) -> None:
    log_file.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def record(job: dict, status: str, note: str = "", log_file: Path = LOG_FILE) -> None:
    log = load_log(log_file)
    log[job["id"]] = {
        "title": job.get("title"),
        "company": job.get("companyName"),
        "link": job.get("link"),
        "status": status,
        "note": note,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    save_log(log, log_file)
    print(f"Logged: {job['id']} -> {status}" + (f" ({note})" if note else ""))


def profile_field(profile: str, name: str) -> str:
    """The value of a `Name: value` line in profile.md, or "" if it's missing or blank."""
    m = re.search(rf"^[ \t]*{re.escape(name)}:[ \t]*(\S.*)$", profile, re.M | re.I)
    return m.group(1).strip() if m else ""


def read_profile() -> str:
    """profile.md's text; exits with a setup hint if it hasn't been created yet."""
    if not PROFILE_FILE.exists() or not PROFILE_FILE.read_text(encoding="utf-8").strip():
        raise SystemExit(f"{PROFILE_FILE.name} is missing or empty — run /setup in Claude Code, or copy "
                         "profile.md.example to profile.md and fill it in.")
    return PROFILE_FILE.read_text(encoding="utf-8")


def candidate_name() -> str:
    """The candidate's full name from profile.md's `Name:` line ("" if not filled in)."""
    try:
        return profile_field(PROFILE_FILE.read_text(encoding="utf-8"), "Name")
    except FileNotFoundError:
        return ""


def cv_upload_name() -> str:
    """The file name an employer sees for the CV: "<First name>'s CV.pdf"."""
    name = candidate_name()
    return f"{name.split()[0]}'s CV.pdf" if name else "CV.pdf"


def cover_upload_name() -> str:
    """The file name an employer sees for the cover letter: "<Full name> - Cover Letter.pdf"."""
    name = candidate_name()
    return f"{name} - Cover Letter.pdf" if name else "Cover Letter.pdf"
