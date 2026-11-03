#!/usr/bin/env python3
"""Send the Telegram flow's DM applications through the Telegram API (Telethon), logged in as you.

Takes the DMs telegram_dm_plan.py builds (pending telegram-method jobs whose CV, cover letter and
message all exist) and sends each one as your Telegram account: the message as the caption of the
CV + cover letter PDFs. Each sent DM is logged as applied in telegram_log.json.

Pacing protects your account from Telegram's anti-spam limit on messaging strangers: --delay
seconds between DMs (default 90) and at most --max per run (default 10). A flood-limit error stops
the run immediately; the rest stay pending for the next run.

Needs, in .env (gitignored): TELEGRAM_API_ID and TELEGRAM_API_HASH from my.telegram.org → API
development tools. Log in once, interactively (Telegram sends a code to your app):

    python3 scripts/send_telegram_dms.py --login

The session is saved as .telegram.session (gitignored). Exit codes: 0 done, 3 not set up (no
credentials or not logged in) — run_telegram_flow.py then falls back to sending through Telegram
Web in Chrome.

Usage:
    python3 scripts/send_telegram_dms.py --login
    python3 scripts/send_telegram_dms.py --dry-run
    python3 scripts/send_telegram_dms.py
"""
import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, load_jobs, record, select_job  # noqa: E402
from fetch_telegram_jobs import TELEGRAM_JOBS_FILE, TELEGRAM_LOG_FILE  # noqa: E402
from send_telegram_emails import load_env  # noqa: E402

SESSION = ROOT / ".telegram"  # Telethon appends ".session"
PLAN_SCRIPT = ROOT / "scripts" / "telegram_dm_plan.py"
NOT_SET_UP = 3


def plan() -> list[dict]:
    out = subprocess.run([sys.executable, str(PLAN_SCRIPT)], capture_output=True, text=True, check=True)
    if out.stderr.strip():
        print(out.stderr.strip())
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


async def run(args, api_id: int, api_hash: str) -> int:
    from telethon import TelegramClient, errors

    client = TelegramClient(str(SESSION), api_id, api_hash)
    if args.login:
        await client.start()  # prompts for phone + code (+ 2FA password) in this terminal
        me = await client.get_me()
        print(f"Logged in as @{me.username or me.first_name}; session saved.")
        await client.disconnect()
        return 0
    await client.connect()
    if not await client.is_user_authorized():
        print("Not logged in to Telegram — run: python3 scripts/send_telegram_dms.py --login")
        await client.disconnect()
        return NOT_SET_UP

    items = plan()[: args.max]
    jobs = load_jobs(TELEGRAM_JOBS_FILE)
    try:
        for n, item in enumerate(items):
            if n:
                await asyncio.sleep(args.delay)
            try:
                await client.send_file(item["to"], item["files"], caption=item["text"], force_document=True)
            except errors.FloodWaitError as e:
                print(f"Telegram rate limit: wait {e.seconds}s. Stopping; the rest stay pending.")
                break
            except errors.PeerFloodError:
                print("Telegram flagged too many messages to strangers. Stopping; try again in a day or two.")
                break
            except (ValueError, errors.RPCError) as e:
                print(f"FAILED {item['id']} -> {item['to']}: {e}")
                continue
            record(select_job(jobs, item["id"]), "applied",
                   f"Telegram DM to {item['to']} with tailored CV + cover letter", TELEGRAM_LOG_FILE)
    finally:
        await client.disconnect()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=int, default=90, help="Seconds between DMs (default 90)")
    parser.add_argument("--max", type=int, default=10, help="Most DMs per run (default 10)")
    args = parser.parse_args()

    if args.dry_run:
        for item in plan()[: args.max]:
            print(f"would DM {item['id']} -> {item['to']} | files: {', '.join(Path(f).name for f in item['files'])}")
        return 0
    env = load_env()
    if not env.get("TELEGRAM_API_ID") or not env.get("TELEGRAM_API_HASH"):
        print("TELEGRAM_API_ID / TELEGRAM_API_HASH not set in .env — Telegram API sending isn't set up.")
        return NOT_SET_UP
    try:
        import telethon  # noqa: F401
    except ImportError:
        print("telethon isn't installed — run: .venv/bin/pip install telethon")
        return NOT_SET_UP
    return asyncio.run(run(args, int(env["TELEGRAM_API_ID"]), env["TELEGRAM_API_HASH"]))


if __name__ == "__main__":
    sys.exit(main())
