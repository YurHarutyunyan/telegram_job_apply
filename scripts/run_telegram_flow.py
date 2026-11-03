#!/usr/bin/env python3
"""Run the whole Telegram flow end to end: fetch → prepare → email → DM.

  1. fetch_telegram_jobs.py           new posts for search_config.json's role
  2. prepare_telegram_applications.py tailored CV + cover letter for every pending job
  3. send_telegram_emails.py          emails every complete email-method application, no review
                                      (skipped if Gmail isn't set up in .env)
  4. send_telegram_dms.py             DMs through the Telegram API when it's set up; otherwise the
                                      DMs stay pending for the Telegram Web step of /apply-telegram

Ends with a summary. Its last line is machine-readable for /apply-telegram:

    BROWSER_DMS_PENDING=<n>

n > 0 means DMs are waiting to be sent through Telegram Web (the API isn't set up yet).
Link-method jobs (hh.ru, talanto.work, ...) need your account on those sites; they're listed, not
applied to.

Usage:
    python3 scripts/run_telegram_flow.py
    python3 scripts/run_telegram_flow.py --no-ambiguous      # keep only posts that passed screening
    python3 scripts/run_telegram_flow.py --skip-fetch        # just prepare + send what's pending
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, load_jobs, load_log  # noqa: E402
from fetch_telegram_jobs import TELEGRAM_JOBS_FILE, TELEGRAM_LOG_FILE  # noqa: E402

SCRIPTS = ROOT / "scripts"
NOT_SET_UP = 3


def step(title: str, script: str, *args: str) -> int:
    print(f"\n=== {title} ===", flush=True)
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args]).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-ambiguous", action="store_true",
                        help="Don't keep posts whose work-authorization screen is ambiguous")
    parser.add_argument("--skip-fetch", action="store_true")
    args = parser.parse_args()

    if not args.skip_fetch:
        step("1/4 Fetch new posts", "fetch_telegram_jobs.py", *([] if args.no_ambiguous else ["--include-ambiguous"]))
    step("2/4 Tailored CVs + cover letters", "prepare_telegram_applications.py")
    if step("3/4 Email applications", "send_telegram_emails.py") != 0:
        print("Email step didn't finish (see above) — email jobs stay pending for the next run.")
    dm_code = step("4/4 Telegram DMs (API)", "send_telegram_dms.py")

    jobs, log = load_jobs(TELEGRAM_JOBS_FILE), load_log(TELEGRAM_LOG_FILE)
    pending = [j for j in jobs if j["id"] not in log]
    dm_plan = subprocess.run([sys.executable, str(SCRIPTS / "telegram_dm_plan.py")],
                             capture_output=True, text=True).stdout.splitlines()
    browser_dms = len(dm_plan) if dm_code == NOT_SET_UP else 0
    applied = sum(1 for e in log.values() if e["status"] == "applied")
    print("\n=== Summary ===")
    print(f"Applied so far (all runs): {applied}")
    for method in ("email", "telegram", "link"):
        group = [j for j in pending if j["applyMethod"] == method]
        if group:
            print(f"Pending {method}: {len(group)}")
    for j in (j for j in pending if j["applyMethod"] == "link"):
        print(f"  link: {j.get('companyName') or '?'} — {j['title'][:60]} → {j['applyTarget']}")
    if browser_dms:
        print(f"{browser_dms} DM(s) to send through Telegram Web (Telegram API not set up):")
        for line in dm_plan:
            item = json.loads(line)
            print(f"  {item['id']} → {item['to']}")
    print(f"BROWSER_DMS_PENDING={browser_dms}")


if __name__ == "__main__":
    main()
