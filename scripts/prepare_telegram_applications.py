#!/usr/bin/env python3
"""Prepare each pending Telegram-flow job for applying: a tailored CV plus a cover letter.

For every job in telegram_jobs.json not yet logged in telegram_log.json, runs tailor_cv.py (in the
posting's language — Russian for hh.ru/Habr-style posts) and stages:

  cv/telegram/<id>/<First>'s CV.pdf  the tailored CV under the name an employer may see
  cv/telegram/<id>/cover_letter.txt  paste into the site's cover-letter box, or send to the contact
  cv/telegram/<id>/cover_letter.pdf  the same letter as a PDF, for attaching to an email

An already-prepared job is skipped unless --regenerate. Then prints, per job, where to apply.
Applying itself happens with your own account on the job site (or by writing to the contact);
record the outcome with:

    python3 scripts/log_apply.py --log telegram --job-id <id> --status applied --note "..."

Usage:
    python3 scripts/prepare_telegram_applications.py
    python3 scripts/prepare_telegram_applications.py --job-id telegram-somechannel-1234 --regenerate
    python3 scripts/prepare_telegram_applications.py --lang en
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, cv_upload_name, load_jobs, load_log, select_job  # noqa: E402
from fetch_telegram_jobs import TELEGRAM_JOBS_FILE, TELEGRAM_LOG_FILE  # noqa: E402
from tailor_cv import render_pdf  # noqa: E402

TAILOR_SCRIPT = ROOT / "scripts" / "tailor_cv.py"
TAILORED_DIR = ROOT / "cv" / "tailored"
STAGE_DIR = ROOT / "cv" / "telegram"
UPLOAD_NAME = cv_upload_name()  # "<First name>'s CV.pdf", from profile.md


def cover_pdf(stage: Path) -> None:
    letter = (stage / "cover_letter.txt").read_text(encoding="utf-8")
    render_pdf(letter, stage / "cover_letter.pdf")


def prepare(job: dict, lang: str, regenerate: bool) -> bool:
    stage = STAGE_DIR / job["id"]
    if not regenerate and (stage / UPLOAD_NAME).exists() and (stage / "cover_letter.txt").exists():
        if not (stage / "cover_letter.pdf").exists():
            cover_pdf(stage)
        return True
    for old in TAILORED_DIR.glob(f"*_{job['id']}*"):
        old.unlink()
    result = subprocess.run([sys.executable, str(TAILOR_SCRIPT), "--jobs-file", str(TELEGRAM_JOBS_FILE),
                             "--profile", str(ROOT / "profile.md"), "--output-dir", str(TAILORED_DIR),
                             "--job-id", job["id"], "--pdf", "--cover-letter", "--lang", lang])
    pdf = next(TAILORED_DIR.glob(f"*_{job['id']}.pdf"), None)
    cover = next(TAILORED_DIR.glob(f"*_{job['id']}_cover.txt"), None)
    if result.returncode != 0 or not pdf or not cover:
        print(f"FAILED to tailor {job['id']} — apply to it by hand or retry with --regenerate")
        return False
    stage.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf, stage / UPLOAD_NAME)
    shutil.copyfile(cover, stage / "cover_letter.txt")
    cover_pdf(stage)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job-id")
    parser.add_argument("--lang", choices=("auto", "en", "ru"), default="auto")
    parser.add_argument("--regenerate", action="store_true")
    args = parser.parse_args()

    jobs = load_jobs(TELEGRAM_JOBS_FILE)
    logged = load_log(TELEGRAM_LOG_FILE)
    todo = [select_job(jobs, args.job_id)] if args.job_id else [j for j in jobs if j["id"] not in logged]
    if not todo:
        print("Nothing pending — run scripts/fetch_telegram_jobs.py first.")
        return

    ready = [job for job in todo if prepare(job, args.lang, args.regenerate)]
    print(f"\n{len(ready)} of {len(todo)} ready:")
    for job in ready:
        stage = STAGE_DIR / job["id"]
        flag = " [work authorization unclear — check the posting]" if job.get("screen") == "ambiguous" else ""
        print(f"\n- {job.get('companyName') or '?'} — {job['title']}{flag}\n"
              f"  apply via {job['applyMethod']}: {job['applyTarget']}\n"
              f"  post: {job['postUrl']}\n"
              f"  CV: {stage / UPLOAD_NAME}\n"
              f"  cover letter: {stage / 'cover_letter.txt'}")


if __name__ == "__main__":
    main()
