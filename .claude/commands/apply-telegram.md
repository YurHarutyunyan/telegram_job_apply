---
description: Run the whole Telegram flow — fetch posts, tailor CVs, email applications, send DMs
argument-hint: [optional: --no-ambiguous] [optional: --skip-fetch]
---

Run the Telegram job flow end to end. **The user has authorized sending these applications without
review**: emails and DMs go out as soon as the job's own tailored CV, its cover letter and the
message all exist (the plan scripts check this and never emit an incomplete application). Don't
ask for confirmation per message.

## 1. Run the pipeline

```
.venv/bin/python scripts/run_telegram_flow.py $ARGUMENTS
```

It takes a while (screening and CV tailoring call Claude per job) — run it in the background and
wait for it. It fetches posts, prepares a tailored CV + cover letter per job, **emails** every
email-method application itself, and sends DMs through the Telegram API when that's set up. Its
last line is `BROWSER_DMS_PENDING=<n>`.

If the email step says Gmail isn't set up, tell the user (`GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD`
in `.env`, see `.env.example`) — never ask them to paste the password in
chat.

## 2. DMs through Telegram Web (only while BROWSER_DMS_PENDING > 0)

The Telegram API isn't set up yet, so send the pending DMs from the user's own Telegram Web.

1. Ask the user to log in at https://web.telegram.org in their normal Chrome (or confirm they
   already are) and to leave that window in front while you work. Wait for their go-ahead.
2. `tabs_context_mcp`, open a new tab at `https://web.telegram.org/a/`. Check
   `document.visibilityState` with `javascript_tool`; if it's `"hidden"`, ask the user to click
   that tab and bring the Chrome window to the front (this machine runs GNOME on Wayland, where
   no tool can raise another app's window), wait for their go-ahead, and check again. If you see a login screen, stop and ask the user to
   log in — never enter a phone number, code or password yourself. Leave any "Someone just got
   access… is it you?" banner alone; that's the user's to answer.
3. Get the list: `.venv/bin/python scripts/telegram_dm_plan.py` — one JSON line per DM with
   `id`, `to`, `text`, `files`, `cover_name`. Then for each, one at a time, **at least 90 seconds apart and at
   most 10 per run** (Telegram restricts accounts that message many strangers quickly):
   - Open the chat: click the search box, type the handle without `@`, wait, and click the result
     whose `@username` matches exactly under "Chats and Contacts" / "Global Search". It must be a
     person or business account — if it shows "subscribers" (a channel) or "bot", skip it and log
     it `needs_manual_review` with that reason.
   - **Read the chat's last messages first.** If the user already applied to this vacancy there
     (a CV already sent for it), don't send — log it `duplicate` with a note saying when.
   - Attach both PDFs without a file picker: with `javascript_tool`, append a hidden
     `<input type="file" multiple id="claude-file-drop">` to `document.body`; `find` it and
     `file_upload` the two `files` paths into it; then with `javascript_tool` build a
     `DataTransfer` from its files — re-wrapping the cover letter as
     `new File([f], <the item's cover_name>, {type: "application/pdf"})` — focus
     `#editable-message-text` and dispatch
     `new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})` on it.
     Telegram opens a "Send 2 Files" dialog.
   - Click the dialog's "Add a caption…" field, type `text`, check the dialog shows both files
     and the caption, then click the dialog's send button. Remove the hidden input afterwards.
   - Confirm the message with both files appears in the chat, then log it:
     ```
     .venv/bin/python scripts/log_apply.py --log telegram --job-id <id> --status applied \
         --note "Telegram Web DM to <to> with tailored CV + cover letter"
     ```
   - If anything goes wrong (dialog didn't open, send failed, Telegram shows a limit warning),
     stop the DM loop, log nothing for that job, and report what happened.
4. Close the tab when done.

## 3. Report

One short summary: emails sent, DMs sent, skipped (with reasons), and the link-method jobs still
waiting for the user's accounts on those sites (from the pipeline's summary).
