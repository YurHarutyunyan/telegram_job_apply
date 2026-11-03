#!/usr/bin/env python3
"""Build the application DM for each pending Telegram-method job in the Telegram flow.

For every job in telegram_jobs.json whose applyMethod is "telegram" and that isn't logged in
telegram_log.json yet, checks that all three parts exist — the tailored CV, the cover letter PDF and
a written message — and prints one JSON object per line: id, to (@handle), text, files (absolute
paths of the CV and cover letter), cover_name (the file name the cover letter is sent under). A job missing a part is printed to stderr as NOT READY and left
out; one whose @handle was already sent an application for the same title (a vacancy cross-posted
in two channels) is printed as DUPLICATE and left out.

Both DM senders read this: send_telegram_dms.py (Telegram API) and the /apply-telegram command's
Telegram Web fallback.

Usage:
    python3 scripts/telegram_dm_plan.py
    python3 scripts/telegram_dm_plan.py --job-id telegram-somechannel-1234
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, cover_upload_name, load_jobs, load_log, profile_field, read_profile, select_job  # noqa: E402
from fetch_telegram_jobs import TELEGRAM_JOBS_FILE, TELEGRAM_LOG_FILE  # noqa: E402
from prepare_telegram_applications import STAGE_DIR, UPLOAD_NAME  # noqa: E402
from tailor_cv import detect_lang, job_brief  # noqa: E402
from telegram_email_plan import norm  # noqa: E402


def dm_text(job: dict, profile: str) -> str:
    name, phone, linkedin = (profile_field(profile, f) for f in ("Name", "Phone", "LinkedIn"))
    title, company, channel = job["title"].strip(), job.get("companyName"), job["channel"]
    if detect_lang(job_brief(job)) == "ru":
        name_ru = profile_field(profile, "Name (Russian)") or name
        at = f" в {company}" if company else ""
        return (f"Здравствуйте! Хочу откликнуться на вакансию «{title}»{at} из канала @{channel}. "
                f"Прикладываю резюме и сопроводительное письмо. С уважением, {name_ru}, {phone}, {linkedin}")
    at = f" at {company}" if company else ""
    return (f"Hello! I'd like to apply for the {title} position{at} posted in @{channel}. "
            f"My CV and cover letter are attached. Best regards, {name}, {phone}, {linkedin}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job-id")
    args = parser.parse_args()

    profile = read_profile()
    jobs = load_jobs(TELEGRAM_JOBS_FILE)
    logged = load_log(TELEGRAM_LOG_FILE)
    by_id = {j["id"]: j for j in jobs}
    sent = {(by_id[i].get("applyTarget", "").lower(), norm(by_id[i]["title"]))
            for i, e in logged.items() if e["status"] == "applied" and i in by_id}
    todo = ([select_job(jobs, args.job_id)] if args.job_id else
            [j for j in jobs if j["applyMethod"] == "telegram" and j["id"] not in logged])
    for job in todo:
        key = (job["applyTarget"].lower(), norm(job["title"]))
        if key in sent:
            print(f"DUPLICATE {job['id']}: already applied to {job['applyTarget']} for this title", file=sys.stderr)
            continue
        stage = STAGE_DIR / job["id"]
        cv, cover = stage / UPLOAD_NAME, stage / "cover_letter.pdf"
        text = dm_text(job, profile)
        missing = [what for what, ok in (("CV", cv.exists()), ("cover letter", cover.exists()),
                                         ("message", bool(text.strip()))) if not ok]
        if missing:
            print(f"NOT READY {job['id']}: missing {', '.join(missing)}", file=sys.stderr)
            continue
        sent.add(key)  # a cross-post pending in this same run is a duplicate too
        print(json.dumps({"id": job["id"], "to": job["applyTarget"], "text": text,
                          "files": [str(cv.resolve()), str(cover.resolve())],
                          "cover_name": cover_upload_name()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
