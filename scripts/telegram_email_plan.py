#!/usr/bin/env python3
"""Build the application email for each pending email-method job in the Telegram flow.

For every job in telegram_jobs.json whose applyMethod is "email" and that isn't logged in
telegram_log.json yet, checks that all three parts exist — the tailored CV
(cv/telegram/<id>/<First name>'s CV.pdf), the cover letter PDF (cover_letter.pdf) and a written message —
and prints one JSON object per line: id, to, subject, body, attachments (absolute paths). A job
missing any part is printed to stderr as NOT READY and left out, so it never gets sent half-done.

The message is short and in the posting's language; the substance is in the attached CV and cover
letter. Run prepare_telegram_applications.py first.

Usage:
    python3 scripts/telegram_email_plan.py
    python3 scripts/telegram_email_plan.py --job-id telegram-somechannel-1234
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, load_jobs, load_log, profile_field, read_profile, select_job  # noqa: E402
from fetch_telegram_jobs import TELEGRAM_JOBS_FILE, TELEGRAM_LOG_FILE  # noqa: E402
from prepare_telegram_applications import STAGE_DIR, UPLOAD_NAME  # noqa: E402
from tailor_cv import detect_lang, job_brief  # noqa: E402

def norm(title: str) -> str:
    return re.sub(r"\W+", " ", title or "").strip().lower()


def message(job: dict, profile: str) -> tuple[str, str]:
    name, phone, linkedin = (profile_field(profile, f) for f in ("Name", "Phone", "LinkedIn"))
    title, company, channel = job["title"].strip(), job.get("companyName"), job["channel"]
    if detect_lang(job_brief(job)) == "ru":
        name_ru = profile_field(profile, "Name (Russian)") or name
        at = f" в {company}" if company else ""
        subject = f"Отклик на вакансию «{title}», {name_ru}"
        body = (f"Здравствуйте!\n\nХочу откликнуться на вакансию «{title}»{at}, опубликованную в "
                f"Telegram-канале @{channel}.\n\nПрикладываю резюме и сопроводительное письмо. "
                f"Буду рад ответить на вопросы и обсудить детали.\n\nС уважением,\n{name_ru}\n{phone}\n{linkedin}")
    else:
        at = f" at {company}" if company else ""
        subject = f"Application: {title}, {name}"
        body = (f"Hello,\n\nI'd like to apply for the {title} position{at} that was posted in the "
                f"@{channel} Telegram channel.\n\nPlease find my CV and cover letter attached. I'd be glad "
                f"to answer any questions.\n\nBest regards,\n{name}\n{phone}\n{linkedin}")
    return subject, body


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job-id")
    args = parser.parse_args()

    profile = read_profile()
    jobs = load_jobs(TELEGRAM_JOBS_FILE)
    logged = load_log(TELEGRAM_LOG_FILE)
    todo = ([select_job(jobs, args.job_id)] if args.job_id else
            [j for j in jobs if j["applyMethod"] == "email" and j["id"] not in logged])
    by_id = {j["id"]: j for j in jobs}
    sent = {(by_id[i].get("applyTarget", "").lower(), norm(by_id[i]["title"]))
            for i, e in logged.items() if e["status"] == "applied" and i in by_id}
    for job in todo:
        key = (job["applyTarget"].lower(), norm(job["title"]))
        if key in sent:
            # The same vacancy cross-posted in two channels: never email the same address twice for it.
            print(f"DUPLICATE {job['id']}: already applied to {job['applyTarget']} for this title", file=sys.stderr)
            continue
        stage = STAGE_DIR / job["id"]
        cv, cover = stage / UPLOAD_NAME, stage / "cover_letter.pdf"
        subject, body = message(job, profile)
        missing = [what for what, ok in (("CV", cv.exists()), ("cover letter", cover.exists()),
                                         ("message", bool(body.strip()))) if not ok]
        if missing:
            print(f"NOT READY {job['id']}: missing {', '.join(missing)}", file=sys.stderr)
            continue
        sent.add(key)  # a cross-post pending in this same run is a duplicate too
        print(json.dumps({"id": job["id"], "to": job["applyTarget"], "subject": subject, "body": body,
                          "attachments": [str(cv.resolve()), str(cover.resolve())]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
