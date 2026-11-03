---
description: Set up telegram-autoapply step by step — dependencies, profile, search config, Gmail, Telegram API (not the channel list)
argument-hint: [optional: step number to start from, e.g. 4]
---

Walk the user through setting up this project, one step at a time. Run from the project root.
If `$ARGUMENTS` is a step number, start there. After each step, say in one line what was done and
move on; stop and ask only where a step says so. Never ask the user to paste a password, App
Password, API hash or Telegram login code into the chat: they type secrets into `.env` themselves,
or into a command they run with `! <command>`.

**Do not touch `telegram_channels.json`.** Choosing channels is the user's job; at the end, remind
them to fill it in (format in `telegram_channels.json.example`).

## 1. Prerequisites

Check, and report what's missing with how to install it (don't install system packages yourself):
- `python3 --version` is 3.10 or newer.
- Google Chrome exists at `/usr/bin/google-chrome` (Linux) or
  `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` (macOS). It renders the PDFs.
- `claude --version` works (`claude -p` does the screening and CV tailoring).

## 2. Python environment

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```
Skip the venv creation if `.venv/` already exists; still run the install.

Then create the local data files the repo leaves out (they're gitignored; never overwrite one
that already exists, it may hold the user's data):
```
mkdir -p cv/tailored cv/telegram
[ -e telegram_jobs.json ] || echo '[]' > telegram_jobs.json
[ -e telegram_log.json ] || echo '{}' > telegram_log.json
[ -e screened_jobs.json ] || echo '{}' > screened_jobs.json
[ -e telegram_channels.json ] || echo '{"channels": []}' > telegram_channels.json
```
`telegram_channels.json` is created empty only; the channel list stays the user's to fill in.

## 3. Candidate profile (`profile.md`)

This is the only source of facts the CVs and cover letters may use, so it must be true and complete.

If `profile.md` doesn't exist, create it in step 3 below (start from `profile.md.example`).

1. If `profile.md` already has content, show a short summary and ask whether to keep it. If kept, go
   to step 4.
2. Ask the user for their current CV: a file path (PDF, DOCX, Markdown or text) or pasted text. If
   they have none, interview them instead, a few questions at a time, following the sections of
   `profile.md.example`.
3. Read the CV (for a PDF: `pdftotext`, or the Read tool) and write `profile.md` in the structure of
   `profile.md.example`: the `Name:`, `Name (Russian):`, `Email:`, `Phone:`, `Location:`,
   `LinkedIn:`, `Website/GitHub:` and `Work Authorization:` lines, then Summary, Skills,
   Experience, Education, Languages. Copy facts; never add a skill, number or date the CV doesn't
   state. Leave a field blank rather than guess.
4. `Work Authorization:` is required (the citizenship screen uses it). If the CV doesn't say, ask:
   citizenship, where they can legally work, whether they need sponsorship.
5. Ask whether they want a Russian spelling of their name for Russian-language posts
   (`Name (Russian):`); optional.
6. Show the finished `profile.md` and ask them to confirm or correct it. Apply corrections.

## 4. What to search for (`search_config.json`)

Ask two questions: which role/stack they're looking for (e.g. Java, Python, Go, frontend React),
and which country they live in (and city, if they'd take on-site or hybrid jobs there). If `search_config.json`
already exists, show it and ask whether to keep it. Otherwise write
`search_config.json` following `search_config.json.example` (drop the `_`-prefixed help keys):
- `role_label`: short name, e.g. `"Python"`.
- `role_regex`: the language plus its main frameworks, word-bounded (`\bjava\b` must not match
  "JavaScript").
- `other_lang_regex` / `other_stack_title_regex`: the example's lists **with the user's own stack
  removed** and the example's stack (Python, Django, ...) added back if it isn't theirs.
- `home_country` and `home_location_regex`: the country, its main cities, and their Russian
  spellings too (most Telegram job channels post in Russian), e.g. `poland|warsaw|польш|варшав`.
- `restricted_regex`: keep the example's (it drops posts limited to Russian residents/citizens)
  unless the user lives in Russia, in which case set it to `"EEA only"`.
- `cv_extra_rules`: ask whether there's anything the CV writer must never claim or must word a
  specific way (e.g. "never claim Go experience, I only touched it once"). Empty list if not.

Check it compiles and behaves:
```
.venv/bin/python -c "import sys; sys.path.insert(0,'scripts'); import common; c=common.load_config(); [common.config_re(k) for k in c if k.endswith('_regex') and c[k]]; print(common.is_target_role({'title': input('sample title: ')}))"
```
Test with one title that should match and one that shouldn't (feed it via `echo ... |`). Show the
config to the user and ask them to confirm.

## 5. Gmail sending (`.env`)

1. If `.env` doesn't exist: `cp .env.example .env && chmod 600 .env`.
2. Tell the user to create a Gmail **App Password** (myaccount.google.com → Security → 2-Step
   Verification → App passwords; needs 2-Step Verification on) and to put `GMAIL_ADDRESS` and
   `GMAIL_APP_PASSWORD` into `.env` themselves, in their editor. Wait for them to say done.
3. Test the login without printing anything secret:
   ```
   .venv/bin/python -c "import sys,smtplib; sys.path.insert(0,'scripts'); from send_telegram_emails import load_env; e=load_env(); s=smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=30); s.login(e['GMAIL_ADDRESS'], e['GMAIL_APP_PASSWORD'].replace(' ','')); s.quit(); print('Gmail login OK')"
   ```
   On failure, show only the error type/message (never the env values) and help them fix it.
   If they don't want email sending at all, skip this step; email-method jobs just stay pending.

## 6. Telegram API for DMs (optional)

Ask whether they want DMs sent through the Telegram API. If not, skip: `/apply-telegram` sends DMs
through Telegram Web in Chrome instead.

If yes: they get `api_id` and `api_hash` at https://my.telegram.org → API development tools, put
them into `.env` as `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` themselves, then log in by running
this themselves (it asks for their phone number and a code, which must not go through you):
```
! .venv/bin/python scripts/send_telegram_dms.py --login
```

## 7. Check

- `.venv/bin/python scripts/backend.py` prints the active engine (`claude` by default).
- Every script imports: `cd scripts && ../.venv/bin/python -c "import fetch_telegram_jobs, prepare_telegram_applications, telegram_email_plan, telegram_dm_plan, send_telegram_emails, send_telegram_dms, run_telegram_flow"`.
- `.venv/bin/python scripts/send_telegram_emails.py --dry-run` runs (expect "Nothing to send.").

Finish with a short summary of what's set up and what's skipped, then:
- Confirm these now exist: `profile.md`, `search_config.json`, `telegram_channels.json`,
  `telegram_jobs.json`, `telegram_log.json`, `screened_jobs.json`, `cv/tailored/`, `cv/telegram/`.
- Fill in `telegram_channels.json` with the channels to read (see `telegram_channels.json.example`).
- Try a fetch without saving: `.venv/bin/python scripts/fetch_telegram_jobs.py --dry-run`.
- Run everything with `/apply-telegram`. It sends applications **without a review step**.
