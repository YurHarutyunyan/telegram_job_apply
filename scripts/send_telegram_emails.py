#!/usr/bin/env python3
"""Send the Telegram flow's email applications from your Gmail, then log each one as applied.

Takes the emails telegram_email_plan.py builds (pending email-method jobs whose CV, cover letter
PDF and message all exist — anything incomplete is never sent) and sends each one over Gmail SMTP
with the job's tailored CV and cover letter attached. Each successful send is logged in
telegram_log.json, so re-running never emails the same recruiter twice for a job.

Credentials come from .env at the repo root (gitignored), which you fill in yourself:

    GMAIL_ADDRESS=you@gmail.com
    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

Replies are directed (Reply-To) to the Email in profile.md — the address printed on the CV — when
it differs from GMAIL_ADDRESS.

An app password is a 16-character password Google generates for one app: myaccount.google.com →
Security → 2-Step Verification (must be on) → App passwords. It only allows sending/reading mail,
and you can revoke it there at any time.

Usage:
    python3 scripts/send_telegram_emails.py --dry-run    # show what would be sent, send nothing
    python3 scripts/send_telegram_emails.py              # send all pending
    python3 scripts/send_telegram_emails.py --job-id telegram-somechannel-1234
"""
import argparse
import json
import os
import re
import smtplib
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, cover_upload_name, load_jobs, read_profile, record, select_job  # noqa: E402
from fetch_telegram_jobs import TELEGRAM_JOBS_FILE, TELEGRAM_LOG_FILE  # noqa: E402

ENV_FILE = ROOT / ".env"
PLAN_SCRIPT = ROOT / "scripts" / "telegram_email_plan.py"
COVER_NAME = cover_upload_name()  # "<Full name> - Cover Letter.pdf", from profile.md


def load_env() -> dict:
    env = dict(os.environ)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env


def plan(job_id: str | None) -> list[dict]:
    cmd = [sys.executable, str(PLAN_SCRIPT)] + (["--job-id", job_id] if job_id else [])
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    if out.stderr.strip():
        print(out.stderr.strip())  # NOT READY lines: these are skipped, never sent half-done
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


def profile_email() -> str | None:
    m = re.search(r"^\s*Email:\s*(\S+@\S+)", read_profile(), re.M | re.I)
    return m.group(1) if m else None


def build(item: dict, sender: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, item["to"], item["subject"]
    reply_to = profile_email()
    if reply_to and reply_to.lower() != sender.lower():
        msg["Reply-To"] = reply_to  # replies go to the address printed on the CV
    msg.set_content(item["body"])
    cv, cover = (Path(p) for p in item["attachments"])
    msg.add_attachment(cv.read_bytes(), maintype="application", subtype="pdf", filename=cv.name)
    msg.add_attachment(cover.read_bytes(), maintype="application", subtype="pdf", filename=COVER_NAME)
    return msg


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job-id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    read_profile()  # exits with a setup hint before the plan step fails on a missing profile.md
    items = plan(args.job_id)
    if not items:
        print("Nothing to send.")
        return
    if args.dry_run:
        for item in items:
            print(f"would send {item['id']} -> {item['to']} | {item['subject']} | "
                  f"attachments: {', '.join(Path(p).name for p in item['attachments'])}")
        return

    env = load_env()
    sender, password = env.get("GMAIL_ADDRESS"), env.get("GMAIL_APP_PASSWORD")
    if not sender or not password:
        print(f"Gmail isn't set up: add GMAIL_ADDRESS and GMAIL_APP_PASSWORD to {ENV_FILE} (see --help).")
        sys.exit(3)

    jobs = load_jobs(TELEGRAM_JOBS_FILE)
    sent = 0
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(sender, password.replace(" ", ""))
        for item in items:
            try:
                smtp.send_message(build(item, sender))
            except smtplib.SMTPException as e:
                print(f"FAILED {item['id']} -> {item['to']}: {e}")
                continue
            record(select_job(jobs, item["id"]), "applied",
                   f"emailed {item['to']} with tailored CV + cover letter", TELEGRAM_LOG_FILE)
            sent += 1
    print(f"Sent {sent} of {len(items)}.")


if __name__ == "__main__":
    main()
