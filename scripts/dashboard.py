#!/usr/bin/env python3
"""Local dashboard for the Telegram flow: applied history, duplicates, new postings, and an Apply button.

Serves dashboard/index.html and a small JSON API on 127.0.0.1 only. Every action goes through the
existing scripts, so telegram_log.json stays the single source of truth:

  - Apply (email job): prepares the CV + cover letter if missing, then send_telegram_emails.py --job-id
    (Gmail SMTP from .env; the duplicate check in telegram_email_plan.py still applies).
  - Apply (link / Telegram DM job): opens the link or chat in your browser; you apply there and click
    "Mark applied", which calls log_apply.py.
  - Prepare CV: prepare_telegram_applications.py --job-id
  - Fetch new posts: fetch_telegram_jobs.py

Long actions run one at a time in the background; the page polls their output.

Usage:
    .venv/bin/python scripts/dashboard.py            # http://127.0.0.1:8765
    .venv/bin/python scripts/dashboard.py --port 9000
"""
import argparse
import json
import re
import subprocess
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, DuplicateIndex, cover_upload_name, load_jobs, load_log  # noqa: E402
from fetch_telegram_jobs import TELEGRAM_JOBS_FILE, TELEGRAM_LOG_FILE  # noqa: E402
from prepare_telegram_applications import STAGE_DIR, UPLOAD_NAME  # noqa: E402

PAGE = ROOT / "dashboard" / "index.html"
PY = sys.executable
SCRIPTS = ROOT / "scripts"
MARK_STATUSES = ("applied", "closed", "rejected", "needs_manual_review", "duplicate")

_tasks: dict[str, dict] = {}
_run_lock = threading.Lock()  # one script at a time: they all write the same JSON files


# --- state ---------------------------------------------------------------------------------------

def _files(job_id: str) -> dict:
    stage = STAGE_DIR / job_id
    return {"cv": (stage / UPLOAD_NAME).exists(), "cover": (stage / "cover_letter.pdf").exists()}


def _target_url(job: dict) -> str | None:
    target = job.get("applyTarget") or ""
    if job.get("applyMethod") == "telegram":
        return f"https://t.me/{target.lstrip('@')}" if target else None
    if job.get("applyMethod") == "email":
        return f"mailto:{target}" if target else None
    return target or job.get("link")


def build_state() -> dict:
    jobs = load_jobs(TELEGRAM_JOBS_FILE) if TELEGRAM_JOBS_FILE.exists() else []
    log = load_log(TELEGRAM_LOG_FILE)
    by_id = {j["id"]: j for j in jobs}

    # Same rule as the plan scripts: compare against applied jobs, then against earlier unlogged
    # jobs (oldest first), so the first copy of a cross-post stays "new" and the rest are duplicates.
    seen = DuplicateIndex()
    for jid, entry in log.items():
        if entry.get("status") == "applied" and jid in by_id:
            seen.add(by_id[jid], "applied")
    dup_reason: dict[str, str] = {}
    for job in sorted((j for j in jobs if j["id"] not in log), key=lambda j: j.get("postedAt") or ""):
        reason = seen.find(job)
        if reason:
            dup_reason[job["id"]] = reason.replace("same vacancy queued in this run", "same vacancy as a job in New postings")
        else:
            seen.add(job, "queued")

    rows = []
    for job in jobs:
        entry = log.get(job["id"])
        if entry:
            status = entry.get("status")
            bucket = {"applied": "applied", "duplicate": "duplicates"}.get(status, "other")
        elif job["id"] in dup_reason:
            status, bucket = "duplicate", "duplicates"
        else:
            status, bucket = "new", "new"
        rows.append({
            "id": job["id"],
            "title": job.get("title"),
            "company": None if job.get("companyName") in (None, "None") else job.get("companyName"),
            "channel": job.get("channel"),
            "postUrl": job.get("postUrl"),
            "postedAt": job.get("postedAt"),
            "location": None if job.get("location") in (None, "None") else job.get("location"),
            "salary": None if job.get("salary") in (None, "None") else job.get("salary"),
            "method": job.get("applyMethod"),
            "target": job.get("applyTarget"),
            "targetUrl": _target_url(job),
            "screen": job.get("screen"),
            "description": job.get("descriptionText") or "",
            "status": status,
            "bucket": bucket,
            "note": (entry or {}).get("note") or dup_reason.get(job["id"], ""),
            "at": (entry or {}).get("at"),
            "files": _files(job["id"]),
        })
    # Log entries whose job is no longer in telegram_jobs.json still belong in the history.
    for jid, entry in log.items():
        if jid not in by_id:
            st = entry.get("status")
            rows.append({"id": jid, "title": entry.get("title"), "company": entry.get("company"),
                         "postUrl": entry.get("link"), "status": st, "note": entry.get("note"),
                         "at": entry.get("at"), "bucket": {"applied": "applied", "duplicate": "duplicates"}.get(st, "other"),
                         "files": _files(jid), "method": None, "orphan": True})
    return {"jobs": rows, "coverName": cover_upload_name(), "cvName": UPLOAD_NAME,
            "busy": _run_lock.locked()}


# --- background tasks ------------------------------------------------------------------------------

def _run(task: dict, steps: list[list[str]]) -> None:
    with _run_lock:
        task["state"] = "running"
        for cmd in steps:
            task["output"] += f"$ {' '.join(Path(c).name if c.startswith(str(ROOT)) else c for c in cmd)}\n"
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                task["output"] += line
            proc.wait()
            task["exit"] = proc.returncode
            if proc.returncode != 0:
                task["state"] = "failed"
                return
        task["state"] = "done"


def start_task(label: str, steps: list[list[str]], job_id: str | None = None) -> dict:
    task = {"id": uuid.uuid4().hex[:12], "label": label, "jobId": job_id, "state": "queued",
            "output": "", "exit": None}
    _tasks[task["id"]] = task
    threading.Thread(target=_run, args=(task, steps), daemon=True).start()
    return task


def apply_steps(job: dict) -> list[list[str]]:
    steps = []
    if not all(_files(job["id"]).values()):
        steps.append([PY, str(SCRIPTS / "prepare_telegram_applications.py"), "--job-id", job["id"]])
    steps.append([PY, str(SCRIPTS / "send_telegram_emails.py"), "--job-id", job["id"]])
    return steps


# --- HTTP ----------------------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _job(self, job_id: str) -> dict | None:
        jobs = load_jobs(TELEGRAM_JOBS_FILE) if TELEGRAM_JOBS_FILE.exists() else []
        return next((j for j in jobs if j["id"] == job_id), None)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/state":
            return self._json(build_state())
        if m := re.fullmatch(r"/api/task/(\w+)", path):
            task = _tasks.get(m.group(1))
            return self._json(task) if task else self._json({"error": "no such task"}, 404)
        if m := re.fullmatch(r"/files/([\w.-]+)/(cv|cover|cover_txt)", path):
            job_id, kind = unquote(m.group(1)), m.group(2)
            name = {"cv": UPLOAD_NAME, "cover": "cover_letter.pdf", "cover_txt": "cover_letter.txt"}[kind]
            f = STAGE_DIR / job_id / name
            if f.resolve().parent.parent == STAGE_DIR.resolve() and f.exists():
                ctype = "text/plain; charset=utf-8" if kind == "cover_txt" else "application/pdf"
                return self._send(200, f.read_bytes(), ctype)
            return self._json({"error": "file not found"}, 404)
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        # Same-origin only: the API runs scripts that send email.
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            return self._json({"error": "cross-origin request refused"}, 403)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad JSON"}, 400)
        path = urlparse(self.path).path

        if path == "/api/fetch":
            return self._json(start_task("Fetch new posts", [[PY, str(SCRIPTS / "fetch_telegram_jobs.py")]]))

        job = self._job(str(body.get("id", "")))
        if job is None:
            return self._json({"error": "unknown job id"}, 404)
        logged = load_log(TELEGRAM_LOG_FILE).get(job["id"])

        if path == "/api/apply":
            if job.get("applyMethod") != "email":
                return self._json({"error": "only email jobs are sent from here; open the link and mark it applied"}, 400)
            if logged and logged.get("status") == "applied":
                return self._json({"error": "already applied"}, 409)
            return self._json(start_task(f"Apply: {job.get('title')}", apply_steps(job), job["id"]))
        if path == "/api/prepare":
            steps = [[PY, str(SCRIPTS / "prepare_telegram_applications.py"), "--job-id", job["id"]]
                     + (["--regenerate"] if body.get("regenerate") else [])]
            return self._json(start_task(f"Prepare CV: {job.get('title')}", steps, job["id"]))
        if path == "/api/mark":
            status = body.get("status")
            if status not in MARK_STATUSES:
                return self._json({"error": f"status must be one of {MARK_STATUSES}"}, 400)
            note = str(body.get("note") or "").strip() or "marked from dashboard"
            out = subprocess.run([PY, str(SCRIPTS / "log_apply.py"), "--job-id", job["id"], "--status", status,
                                  "--note", note], cwd=ROOT, capture_output=True, text=True)
            if out.returncode:
                return self._json({"error": out.stderr or out.stdout}, 500)
            return self._json({"ok": True, "output": out.stdout})
        self._json({"error": "not found"}, 404)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Dashboard: {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
