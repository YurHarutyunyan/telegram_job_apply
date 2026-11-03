# telegram_job_apply

Finds developer job posts in public Telegram channels and applies to them. You choose the role
(Java, Python, Go, ...) and say where you live; it keeps posts that fit, writes a CV and a cover
letter tailored to each one in the post's language (Russian or English), using only facts from
your `profile.md`, then emails the application or DMs the recruiter on Telegram with both PDFs
attached.

**It sends applications without a review step.** Emails and DMs go out as soon as the job's
tailored CV, its cover letter and the message all exist. Check your profile and config before the
first run.

## Quick setup with Claude Code

You need [Claude Code](https://claude.com/claude-code) installed and logged in, Python 3.10+ and
Google Chrome. Then paste this into a terminal. It clones the repo and opens Claude Code with
`/setup` already running:

```bash
git clone https://github.com/YurHarutyunyan/telegram_job_apply.git && cd telegram_job_apply && claude "/setup"
```

Already cloned? From the repo folder, run `claude "/setup"`, or type `/setup` inside Claude Code.

`/setup` walks you through everything below: it checks the prerequisites, installs the Python
packages, builds `profile.md` from your existing CV (and asks you to confirm it), writes
`search_config.json` from two questions (your stack, your country), tests your Gmail login and,
if you want, logs in to the Telegram API. It never asks for passwords in the chat; you type those
into `.env` yourself. The only thing it leaves to you is the list of channels to read.

## Manual setup

You need Linux or macOS, Python 3.10+, Google Chrome (renders the PDFs) and
[Claude Code](https://claude.com/claude-code), logged in (`claude -p` does the screening and tailoring).

1. **Python packages**
   ```
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
2. **Your profile**: `cp profile.md.example profile.md` and fill it in. It's the only source the
   CVs and cover letters may use, so put in everything true you'd want mentioned and nothing else.
   `Work Authorization:` is required. `Name (Russian):` is optional and signs Russian-language
   messages.
3. **What to search for**: `cp search_config.json.example search_config.json` and edit it. The
   `_`-prefixed keys explain each field:
   - `role_label`, `role_regex`: your stack, e.g. `\bpython\b|\bdjango\b`.
   - `other_lang_regex`, `other_stack_title_regex`: other stacks, used to drop posts that aren't
     yours. Take your own stack out of these lists.
   - `home_country`, `home_location_regex`: where you live. On-site/hybrid posts elsewhere are
     dropped, and so are posts open only to a country list without yours. Add Russian spellings;
     most of these channels post in Russian.
   - `restricted_regex` (optional): post text meaning "residents/citizens of X only".
   - `cv_extra_rules` (optional): extra rules for the CV writer, e.g. a skill never to claim.
4. **Gmail**: `cp .env.example .env && chmod 600 .env`, then set `GMAIL_ADDRESS` and
   `GMAIL_APP_PASSWORD`. That's a Gmail
   [App Password](https://myaccount.google.com/apppasswords) (needs 2-Step Verification), not
   your login password.
5. **Telegram API (optional)**: to send DMs from a script, set `TELEGRAM_API_ID` /
   `TELEGRAM_API_HASH` in `.env` (from [my.telegram.org](https://my.telegram.org) → API
   development tools) and log in once with `.venv/bin/python scripts/send_telegram_dms.py --login`.
   Without it, `/apply-telegram` sends DMs through Telegram Web in your Chrome.
6. **Data files**: `mkdir -p cv/tailored cv/telegram`. The job, log and cache files
   (`telegram_jobs.json`, `telegram_log.json`, `screened_jobs.json`) are created on the first run.
7. **Channels**: `cp telegram_channels.json.example telegram_channels.json` and list the public
   channels to read: a username, or `{"name": "busychannel", "pages": 45}` to read further back
   (about 20 posts per page).

## Run

Inside Claude Code (this also sends DMs through Telegram Web):

```
claude
> /apply-telegram
```

Or without the Telegram Web part:

```
.venv/bin/python scripts/run_telegram_flow.py                 # everything
.venv/bin/python scripts/run_telegram_flow.py --skip-fetch    # only prepare + send what's pending
.venv/bin/python scripts/fetch_telegram_jobs.py --dry-run     # see what a fetch would keep
.venv/bin/python scripts/send_telegram_emails.py --dry-run    # see what would be emailed
```

## Flow

```
telegram_channels.json + search_config.json
  └─ 1. fetch_telegram_jobs.py           reads t.me/s/<channel> public previews; keeps fresh posts for
  │                                      your role that are remote or in your home location; drops
  │                                      QA/support/other stacks; screens citizenship requirements
  │                                      with an LLM (claude -p, haiku)
  │                                      → telegram_jobs.json (applyMethod: email | telegram | link)
  └─ 2. prepare_telegram_applications.py tailor_cv.py (claude -p, sonnet) per pending job
  │                                      → cv/telegram/<id>/<First>'s CV.pdf, cover_letter.txt/.pdf
  └─ 3. send_telegram_emails.py          Gmail SMTP, for email-method jobs (plan: telegram_email_plan.py)
  └─ 4. send_telegram_dms.py             Telegram API, for DM-method jobs (plan: telegram_dm_plan.py);
                                         without API keys, the /apply-telegram command sends them
                                         from Telegram Web through Claude in Chrome
```

`run_telegram_flow.py` runs all four steps. Link-method jobs (hh.ru, talanto.work,
career.habr.com, ...) are only listed, because they need your account on those sites. Once you've
applied to one by hand, log it:

```
.venv/bin/python scripts/log_apply.py --job-id <id> --status applied --note "hh.ru"
```

Every outcome is recorded in `telegram_log.json` (`applied`, `duplicate`, ...). The same vacancy
cross-posted in several channels is detected by recipient and title, and is sent only once.

`python3 scripts/backend.py cursor` switches screening to the Cursor CLI; `claude` is the default.

## Files

| File | What it is |
|---|---|
| `profile.md` | Your facts. CVs and cover letters are built only from this. |
| `search_config.json` | Role to look for, your home location, extra CV rules. |
| `telegram_channels.json` | Channels to read. |
| `telegram_jobs.json` | Every matching post kept so far. |
| `telegram_log.json` | What happened to each job. Re-runs skip anything logged here. |
| `screened_jobs.json` | Cache of LLM screening verdicts, keyed by post hash. |
| `cv/telegram/<id>/` | The CV and cover letter sent for each job. |
| `cv/tailored/` | Raw tailoring output (`.md`, `.pdf`, `_cover.txt`). |
| `.env` | Gmail / Telegram API credentials. Gitignored; never commit it. |

`profile.md`, `search_config.json`, the job and log files and `cv/` hold your personal data
(contact details, CVs, recruiter contacts). They're all in `.gitignore` and are created
locally by `/setup`; keep it that way.
