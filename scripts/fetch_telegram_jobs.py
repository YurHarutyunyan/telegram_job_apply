#!/usr/bin/env python3
"""Pull job posts for the role in search_config.json from public Telegram channels into telegram_jobs.json (the Telegram flow).

A flow of its own, separate from found_jobs.json / the ATS apply agent: these posts are applied to
on job sites (hh.ru, career.habr.com, getmatch.ru, ...) with your own account there, or by writing
to a recruiter's @contact / email named in the post.

Reads each channel's public web preview (t.me/s/<channel>) — no Telegram login or API key, so only
public channels work. Channels live in telegram_channels.json (local only, gitignored — see telegram_channels.json.example):
{"channels": ["somechannel", ...]}; a busy channel can be {"name": "busychannel", "pages": 45} to
read further back (~20 posts/page).

Per post, in order:
  1. A job post with a target-role title (common.is_target_role, role_regex in search_config.json),
     or a generic developer title ("Backend Engineer") whose vacancy page is mainly that role. QA/testing/support roles, ads, polls
     and webinars are skipped.
  2. Fresh (--max-age-days, default 30, by post date).
  3. Not office/hybrid outside the candidate's home location (home_location_regex), and not
     explicitly restricted to residents/citizens of somewhere else (restricted_regex).
  4. How to apply: the post's vacancy link (hh.ru, Habr Career, getmatch, talanto, ...; a
     teletype.in write-up is resolved to the employer link inside it), else a Telegram @contact that
     is a person (a company channel can't be messaged), else an email in the text. Posts with none are skipped. All contacts found are kept
     in "contacts" either way.
  5. For a vacancy link: the page is fetched; closed/archived/removed vacancies are dropped, and the
     page's description replaces the short post text when it can be extracted.
  6. Citizenship/work-authorization screen, using screening.py's regex, LLM prompt and verdict
     cache. Only "passed" posts are saved — plus "ambiguous" ones with --include-ambiguous (you
     apply to these yourself, so a posting that just doesn't say who may apply can be worth a look;
     they're saved with screen="ambiguous").

Ids are "telegram-<channel>-<message id>". Already-saved ids are refreshed, not duplicated.

Usage:
    python3 scripts/fetch_telegram_jobs.py
    python3 scripts/fetch_telegram_jobs.py --pages 3 --max-age-days 45
    python3 scripts/fetch_telegram_jobs.py --channel somechannel --dry-run
"""
import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, config_re, is_target_role, load_config, load_jobs, load_log, save_jobs  # noqa: E402
from screening import (  # noqa: E402
    hard_exclusion, html_to_text, load_screen_cache, posting_hash, read_work_authorization, save_screen_cache, screen_citizenship,
)

CHANNELS_FILE = ROOT / "telegram_channels.json"
TELEGRAM_JOBS_FILE = ROOT / "telegram_jobs.json"
TELEGRAM_LOG_FILE = ROOT / "telegram_log.json"
PREVIEW_URL = "https://t.me/s/{channel}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/155.0 Safari/537.36")
DEFAULT_PAGES = 2  # ~20 posts per page
DEFAULT_MAX_AGE_DAYS = 30

MESSAGE_RE = re.compile(r'<div class="tgme_widget_message_wrap.*?(?=<div class="tgme_widget_message_wrap|\Z)', re.S)
POST_ID_RE = re.compile(r'data-post="([^"/]+)/(\d+)"')
TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
TIME_RE = re.compile(r'<time datetime="([^"]+)"')
HREF_RE = re.compile(r'href="([^"]+)"')
CONTACT_RE = re.compile(r"(?<![\w/.])@([A-Za-z][A-Za-z0-9_]{4,31})\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# "Удалённо (Москва)", "Формат работы: удалённо", "Формат: офис" — the label prefix is dropped.
LOCATION_RE = re.compile(r"^(?:формат(?: работы)?:\s*)?((?:удал|офис|гибрид|remote|office|hybrid|релокац|relocation).*)", re.I)
SALARY_RE = re.compile(r"[₽$€]|руб|т\.\s?р\.|\busd\b|\beur\b|\d{2,3}[\s\u00a0]?000", re.I)
# Testing/QA roles mention the stack constantly but aren't developer jobs.
QA_TITLE_RE = re.compile(r"\ba?qa\b|\bsdet\b|тестиров|\btest|\bsupport\b|поддержк", re.I)
# Developer titles that don't name a language ("Backend Engineer"): kept only if the vacancy page's
# description is mainly the target role (common.is_target_role).
NEUTRAL_DEV_TITLE_RE = re.compile(r"back-?end|software (engineer|developer)|full[- ]?stack|разработчик|developer", re.I)
ONSITE_RE = re.compile(r"офис|гибрид|office|hybrid", re.I)
REMOTE_RE = re.compile(r"удал|remote", re.I)
HASHTAG_RE = re.compile(r"#(\w+)")
HASHTAG_LINE_RE = re.compile(r"^(?:#\w+[\s·,]*)+$")
COMPANY_LABEL_RE = re.compile(r"^(?:компания|company(?: name)?):\s*(.+)$", re.I)
# Labeled posts: "📝 Title: …", "📍 Location: Remote". Matched after the emoji prefix.
TITLE_LABEL_RE = re.compile(r"^(?:title|position|role|должность|вакансия):\s*(.+)$", re.I)
LOCATION_LABEL_RE = re.compile(r"^(?:location|локация|местоположение):\s*(.+)$", re.I)
BULLET_RE = re.compile(r"^[-–•☑✅🔹▪*]")
# Channel boilerplate appended to every post (a CV-checker ad, a channel-network footer).
FOOTER_RE = re.compile(r"перед отправкой резюме|updatecv\.me|в Telegram \| в VK", re.I)
# Links that never point at a vacancy: the channel network's own navigation, short-link ads, surveys.
NON_VACANCY_HOST_RE = re.compile(r"(^|\.)(t\.me|telegram\.me|clc\.to|anketolog\.ru|updatecv\.me|vk\.com|max\.ru)$", re.I)
EMOJI_PREFIX_RE = re.compile(r"^[^\wА-Яа-яЁё]+")

# Per-site closed markers and description containers (data-qa / class attribute on a <div>).
SITE_RULES = {
    "hh.ru": {"closed": ['data-qa="vacancy-title-archived-text"', 'data-qa="vacancy-archive-description"'],
              "description": 'data-qa="vacancy-description"'},
    "getmatch.ru": {"closed": ["b-notification_danger\"><b>Вакансия в архиве"], "description": None},
    "career.habr.com": {"closed": ["Вакансия в архиве", "Вакансия закрыта"], "description": 'class="vacancy-description__text"'},
}
LD_JSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def ld_job_posting(body: str) -> dict | None:
    """The schema.org JobPosting many job sites embed (RemoteDXB does)."""
    for block in LD_JSON_RE.findall(body):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def fetch(url: str) -> tuple[int, str, str]:
    """(status, final url, body). HTTP errors return their status with an empty body."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.geturl(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, url, ""


def message_text(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def channel_posts(channel: str, pages: int) -> list[dict]:
    posts, before = [], None
    for _ in range(pages):
        url = PREVIEW_URL.format(channel=channel) + (f"?before={before}" if before else "")
        status, _, body = fetch(url)
        if status != 200 or "tgme_widget_message" not in body:
            if not posts:
                print(f"{channel}: no public preview (HTTP {status}) — private channels aren't supported")
            break
        page = []
        for block in MESSAGE_RE.findall(body):
            pid, text, when = POST_ID_RE.search(block), TEXT_RE.search(block), TIME_RE.search(block)
            if not (pid and text and when):
                continue
            page.append({"channel": pid.group(1), "msgId": int(pid.group(2)), "html": text.group(1),
                         "text": message_text(text.group(1)), "postedAt": when.group(1)})
        if not page:
            break
        posts += page
        before = min(p["msgId"] for p in page)
    return posts


def element_by_attr(body: str, attr: str) -> str | None:
    """Inner HTML of the first <div ... attr ...>, matching nested divs."""
    start = body.find(attr)
    if start < 0:
        return None
    start = body.rfind("<div", 0, start)
    depth = 0
    for m in re.finditer(r"<div\b|</div>", body[start:]):
        depth += 1 if m.group(0) == "<div" else -1
        if depth == 0:
            return body[start:start + m.end()]
    return None


def vacancy_link(post: dict) -> str | None:
    for href in HREF_RE.findall(post["html"]):
        href = html.unescape(href)
        host = re.sub(r"^www\.", "", re.sub(r"^https?://([^/]+).*", r"\1", href))
        if href.startswith("http") and not NON_VACANCY_HOST_RE.search(host):
            return href
    return None


def parse_post(post: dict) -> dict:
    """Title / location / salary / company from the post text. Handles the channel formats seen so far:
    title on the first line, hashtags + company + title, one-line
    "TITLE | REMOTE CIS | COMPANY #tags link", and labeled "Title: / Company: / Location:" lines."""
    lines = [ln.strip() for ln in post["text"].splitlines() if ln.strip()]
    content = [ln for ln in lines if not HASHTAG_LINE_RE.match(ln) and not FOOTER_RE.search(ln)]
    tags = {t.lower() for t in HASHTAG_RE.findall(post["text"])}
    title = location = salary = company = None

    first = content[0] if content else ""
    labeled = {}
    for ln in content[:12]:
        bare = EMOJI_PREFIX_RE.sub("", ln).strip()
        for key, rx in (("title", TITLE_LABEL_RE), ("company", COMPANY_LABEL_RE), ("location", LOCATION_LABEL_RE)):
            m = rx.match(bare)
            if m and key not in labeled:
                labeled[key] = m.group(1).strip()
    if "title" in labeled:
        title, company, location = labeled["title"], labeled.get("company"), labeled.get("location")
        salary = next((EMOJI_PREFIX_RE.sub("", ln).strip() for ln in content[1:12]
                       if SALARY_RE.search(ln) and len(ln) < 60), None)
    elif " | " in first:
        parts = [x.strip() for x in re.sub(r"\s*#\w.*$|\s*https?://\S+", "", first).split(" | ") if x.strip()]
        title = EMOJI_PREFIX_RE.sub("", parts[0])
        location = next((x for x in parts[1:] if LOCATION_RE.match(x)), None)
        company = parts[-1] if len(parts) > 2 and not LOCATION_RE.match(parts[-1]) else None
    else:
        idx = next((i for i, ln in enumerate(content[:6])
                    if is_target_role({"title": ln}) and len(ln) < 100 and not BULLET_RE.match(ln)), 0)
        title = EMOJI_PREFIX_RE.sub("", content[idx]).strip() if content else ""
        if idx > 0 and len(content[idx - 1]) < 50 and ":" not in content[idx - 1]:
            company = content[idx - 1]  # hashtag-first posts: the company sits right above the title
        for ln in content[1:12]:
            label = COMPANY_LABEL_RE.match(ln)
            m = LOCATION_RE.match(ln)
            if label and not company:
                company = label.group(1).strip()
            elif m and not location and len(ln) < 80:
                location = m.group(1)
            elif not salary and SALARY_RE.search(ln) and len(ln) < 60:
                salary = EMOJI_PREFIX_RE.sub("", ln).strip()
            elif (not company and ln in content[1:4] and re.search(r"\s[—–-]\s", ln)
                  and not BULLET_RE.match(ln)):  # "Company — what it does"
                head = re.split(r"\s[—–-]\s", ln, 1)[0].strip()
                company = head if len(head) <= 40 and head.lower() not in ("мы", "далее") else None
    if not location:  # fall back to hashtags; remote wins when a post tags several formats
        if tags & {"remote", "удаленка", "удалёнка", "удаленно"}:
            location = "Удалённо"
        elif tags & {"office", "офис", "hybrid", "гибрид"}:
            location = "Офис/гибрид"
    return {"title": title or "", "location": location, "salary": salary, "company": company}


_PERSON_CACHE: dict[str, bool] = {}


def is_person(handle: str) -> bool:
    """True if @handle can be DMed (a person or business account), False for a channel or group —
    job posts often name the company's channel as the "contact", which can't receive messages.
    Read from the public t.me page: channels/groups show a subscriber/member count."""
    name = handle.lstrip("@").lower()
    if name not in _PERSON_CACHE:
        try:
            _, _, body = fetch(f"https://t.me/{name}")
        except Exception:
            body = ""
        extra = re.search(r'<div class="tgme_page_extra">([^<]*)', body)
        _PERSON_CACHE[name] = bool(body) and not (extra and re.search(r"subscriber|member", extra.group(1)))
    return _PERSON_CACHE[name]


def contacts(post: dict) -> tuple[list[str], list[str]]:
    """Recruiter Telegram handles and emails in the text, minus the channel's own handle."""
    own = post["channel"].lower()
    handles = [h for h in CONTACT_RE.findall(post["text"]) if h.lower() != own]
    return ["@" + h for h in dict.fromkeys(handles)], list(dict.fromkeys(EMAIL_RE.findall(post["text"])))


def enrich(link: str) -> tuple[str, str | None, str | None, str]:
    """(status, description, company, apply link) for a vacancy page: status is open / closed /
    unreachable. A teletype.in / telegra.ph write-up is read for its text, and its first outside
    link — the employer's own vacancy page — becomes the apply link (the write-up stays the link
    when it has none)."""
    try:
        status, final_url, body = fetch(link)
    except Exception as e:
        return f"unreachable ({e.__class__.__name__})", None, None, link
    if status in (404, 410):
        return "closed", None, None, link
    if status != 200:
        return f"unreachable (HTTP {status})", None, None, link
    host = re.sub(r"^www\.", "", re.sub(r"^https?://([^/]+).*", r"\1", final_url))
    if host in ("teletype.in", "telegra.ph"):
        article = re.search(r"<article[^>]*>(.*?)</article>", body, re.S)
        inner = article.group(1) if article else body
        outside = [h for h in HREF_RE.findall(inner) if h.startswith("http")
                   and not re.search(r"teletype\.in|telegra\.ph|t\.me/", h)]
        return "open", html_to_text(inner), None, html.unescape(outside[0]) if outside else link
    if host == "remotedxb.com":
        # The JobPosting carries the description; the page's "Hiring model" line says where the
        # employer hires from (e.g. "Open worldwide · the employer states no country restriction"),
        # which the work-authorization screen needs. Its apply link needs a RemoteDXB account.
        posting = ld_job_posting(body) or {}
        until = posting.get("validThrough")
        if until and datetime.fromisoformat(until.replace("Z", "+00:00")) < datetime.now(timezone.utc):
            return "closed", None, None, link
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", body, flags=re.S))))
        hiring = re.search(r"Hiring model (.{0,200}?)(?: Posted | About the role)", text)
        desc = html_to_text(posting.get("description", "")) or None
        if hiring:
            desc = f"Hiring model: {hiring.group(1).strip()}\n\n{desc or ''}"
        org = (posting.get("hiringOrganization") or {}).get("name")
        apply_url = (posting.get("url") or link).replace("\\/", "/")
        return "open", desc, org, apply_url if apply_url.startswith("http") else link
    rules = next((r for h, r in SITE_RULES.items() if host == h or host.endswith("." + h)), None)
    if rules and any(marker in body for marker in rules["closed"]):
        return "closed", None, None, link
    desc = None
    if rules and rules["description"]:
        inner = element_by_attr(body, rules["description"])
        desc = html_to_text(inner) if inner else None
    company = None
    m = re.search(r'data-qa="vacancy-company-name"[^>]*>(.*?)</', body, re.S)
    if m:
        company = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() or None
    return "open", desc, company, link


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--channel", action="append", help="Channel username (repeatable); default: telegram_channels.json")
    parser.add_argument("--pages", type=int, default=DEFAULT_PAGES)
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--include-ambiguous", action="store_true",
                        help="Also save posts whose work-authorization screen came back ambiguous")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be saved, save nothing")
    args = parser.parse_args()

    # Entries are a channel name, or {"name": ..., "pages": N} for busy channels that need deeper paging.
    configured = (json.loads(CHANNELS_FILE.read_text(encoding="utf-8") or "{}").get("channels", [])
                  if CHANNELS_FILE.exists() else [])
    if not configured and not args.channel:
        raise SystemExit(f"No channels to read — list them in {CHANNELS_FILE.name} "
                         "(see telegram_channels.json.example), or pass --channel.")
    channels = ([(c, args.pages) for c in args.channel] if args.channel else
                [(c, args.pages) if isinstance(c, str) else (c["name"], c.get("pages", args.pages)) for c in configured])
    jobs = load_jobs(TELEGRAM_JOBS_FILE)
    by_id = {j["id"]: j for j in jobs}
    logged = load_log(TELEGRAM_LOG_FILE)
    work_auth = read_work_authorization()
    config = load_config()
    home, home_re = config["home_country"], config_re("home_location_regex")
    restricted_re = config_re("restricted_regex") if config.get("restricted_regex") else None
    cache = load_screen_cache()
    now = datetime.now(timezone.utc)
    counts = {"new": 0, "updated": 0}

    for channel, pages in channels:
        posts = channel_posts(channel.lstrip("@").removeprefix("https://t.me/").strip("/"), pages)
        print(f"{channel}: {len(posts)} posts read")
        for post in posts:
            job_id = f"telegram-{post['channel']}-{post['msgId']}"
            post_url = f"https://t.me/{post['channel']}/{post['msgId']}"
            meta = parse_post(post)
            label = f"{meta['company'] or '?'} — {meta['title'][:60]}"
            if QA_TITLE_RE.search(meta["title"]):
                continue
            prefetched = None
            if not is_target_role({"title": meta["title"], "descriptionText": ""}):
                link = vacancy_link(post)
                if not (link and NEUTRAL_DEV_TITLE_RE.search(meta["title"])):
                    continue
                prefetched = enrich(link)  # "Backend Engineer": kept only if the description is mainly the role
                if prefetched[0] != "open" or not is_target_role(
                        {"title": meta["title"], "descriptionText": prefetched[1] or ""}):
                    continue
            if not (meta["location"] or meta["salary"]):
                continue  # articles, digests and quizzes that merely mention the stack
            age = (now - datetime.fromisoformat(post["postedAt"])).days
            if age > args.max_age_days:
                continue
            if job_id in logged:
                print(f"already logged, skipped: {label} ({job_id})")
                continue
            loc = meta["location"] or ""
            if ONSITE_RE.search(loc) and not REMOTE_RE.search(loc) and not home_re.search(loc):
                print(f"skipped (on-site/hybrid outside {home}: {meta['location']}): {label}")
                continue
            if restricted_re and restricted_re.search(post["text"]):
                print(f"skipped (restricted to residents/citizens elsewhere): {label}")
                continue

            link = vacancy_link(post)
            handles, emails = contacts(post)
            description, company = post["text"], meta["company"]
            if link:
                status, page_desc, page_company, link = prefetched or enrich(link)
                if status == "closed":
                    print(f"skipped (vacancy closed/archived): {label} — {link}")
                    continue
                if status != "open":
                    print(f"note: {status} for {link}; using the post text")
                description = page_desc or description
                company = page_company or company
                apply = {"method": "link", "target": link}
            elif any(is_person(h) for h in handles):  # a recruiter you can DM
                apply = {"method": "telegram", "target": next(h for h in handles if is_person(h))}
            elif emails:  # also when the only @handle is the company's channel
                apply = {"method": "email", "target": emails[0]}
            else:
                print(f"skipped (no vacancy link or contact): {label}")
                continue

            job = {
                "id": job_id, "source": "telegram", "channel": post["channel"], "postUrl": post_url,
                "title": meta["title"], "companyName": company, "location": meta["location"] or "",
                "salary": meta["salary"], "link": link or post_url, "applyMethod": apply["method"],
                "applyTarget": apply["target"], "contacts": {"telegram": handles, "email": emails},
                "postedAt": post["postedAt"][:10],
                "descriptionText": description,
            }
            key = posting_hash(job)
            verdict = hard_exclusion(job) or cache.get(key)
            if not verdict:
                verdict, cacheable = screen_citizenship(work_auth, job)
                if cacheable:
                    cache[key] = verdict
            job["screen"] = verdict["status"]
            if verdict["status"] != "passed" and not (args.include_ambiguous and verdict["status"] == "ambiguous"):
                print(f"{verdict['status']} (citizenship): {label} — {verdict.get('reason', '')}")
                continue

            if job_id in by_id:
                by_id[job_id].update(job)
                counts["updated"] += 1
            else:
                jobs.append(job)
                by_id[job_id] = job
                counts["new"] += 1
                print(f"new{' (ambiguous)' if job['screen'] == 'ambiguous' else ''}: {label} "
                      f"[{apply['method']}: {apply['target']}]")

    if not args.dry_run:
        save_screen_cache(cache)
        save_jobs(jobs, TELEGRAM_JOBS_FILE)
    print(f"Summary: {counts['new']} new, {counts['updated']} updated"
          + (" (dry run, nothing saved)" if args.dry_run else f"; telegram_jobs.json has {len(jobs)} jobs"))


if __name__ == "__main__":
    main()
