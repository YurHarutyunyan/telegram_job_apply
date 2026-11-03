#!/usr/bin/env python3
"""Log the outcome of a Telegram-flow application by hand (e.g. a link job you applied to on hh.ru,
or a DM sent through Telegram Web). Every write goes through common.record(), so telegram_log.json
stays the single source of truth for status.

Usage:
    python3 scripts/log_apply.py --job-id telegram-somechannel-1234 --status applied \\
        --note "hh.ru, cover letter pasted"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_jobs, record, select_job  # noqa: E402
from fetch_telegram_jobs import TELEGRAM_JOBS_FILE, TELEGRAM_LOG_FILE  # noqa: E402

STATUSES = ("applied", "needs_manual_review", "closed", "skipped_no_cv", "skipped_wrong_stack", "rejected",
            "ready_to_submit", "duplicate")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--status", required=True, choices=STATUSES)
    parser.add_argument("--note", default="")
    parser.add_argument("--log", choices=("telegram",), default="telegram", help=argparse.SUPPRESS)
    args = parser.parse_args()

    record(select_job(load_jobs(TELEGRAM_JOBS_FILE), args.job_id), args.status, args.note, TELEGRAM_LOG_FILE)


if __name__ == "__main__":
    main()
