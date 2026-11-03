#!/usr/bin/env python3
"""Write a CV (and optionally a cover letter) tailored to one job, using only facts in profile.md.

prepare_telegram_applications.py calls it with telegram_jobs.json for every pending job.

Honesty rule: the model may only select, re-order, re-word and translate facts from profile.md —
never add a skill, metric, date or claim. As a mechanical backstop, every number in the output must
also appear in profile.md (or the job's own title/company name); otherwise the model gets one retry with the offending numbers named, and
if it still invents one the run fails (callers treat that as needs_manual_review, never as "use a
generic CV").

Language: --lang en|ru, or auto (default) — Russian when the posting is mostly Cyrillic.

No em dashes or "--" in the output (user's style rule; strip_dashes() enforces it).

Outputs in --output-dir, named <company>_<title>_<id>:
  .md          the tailored CV
  .pdf         rendered from the .md with headless Chrome (--pdf)
  _cover.txt   a short cover letter for the same job (--cover-letter)

Usage:
    python3 scripts/tailor_cv.py --jobs-file found_jobs.json --profile profile.md \\
        --output-dir cv/tailored --job-id <id> --pdf [--cover-letter] [--lang ru]
"""
import argparse
import html
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_config, load_jobs, select_job  # noqa: E402
import backend  # noqa: E402

TAILOR_MODEL = os.environ.get("TAILOR_MODEL", "sonnet")
CHROME_PATH = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
               if sys.platform == "darwin" else "/usr/bin/google-chrome")
MAX_DESCRIPTION_CHARS = 8000
NUMBER_RE = re.compile(r"\d+")
CYRILLIC_RE = re.compile(r"[а-яё]", re.I)
LATIN_RE = re.compile(r"[a-z]", re.I)

RULES = """Hard rules — breaking any of them makes the output unusable:
- Use ONLY facts stated in the candidate profile. Never add a skill, tool, technology, metric,
  number, date, employer, title, degree or responsibility that is not in the profile, even if the
  job asks for it. If the job wants something the profile lacks, leave it out — do not hint at it.
- You may select, re-order, shorten, merge and re-word profile facts so the ones this job cares
  about come first, and you may translate them.
- Keep job titles, employers, dates, degrees and schools exactly as the profile states them
  (translated if writing in Russian).
- Never use the em dash "—" or a double hyphen "--". Where you'd use one, use a single space (or a
  comma). A dash between two dates ("2019 – 2025") is fine.
- Do not write any digit that does not appear in the profile. Spell out durations in words only
  if the profile does ("two years"); never compute new ones."""
# Candidate-specific rules (e.g. "never claim Go experience") live in search_config.json.
RULES += "".join(f"\n- {r}" for r in load_config(required=False).get("cv_extra_rules", []))

CV_SYSTEM = f"""You tailor a software engineer's CV to a specific job posting.
{RULES}
Output format — Markdown only, no preamble, no code fences, exactly these sections in this order:
# <full name>
<one line: location · email · phone · LinkedIn, from the profile>
## <Summary heading>
<2-3 sentences aimed at this job>
## <Skills heading>
- **<group>:** <comma-separated skills, most relevant to this job first>  (4-6 groups, at most 8 skills each, relevant groups first)
## <Experience heading>
### <title> <employer> | <dates>
- <bullet>  (3-5 bullets per role, most relevant to this job first)
## <Education heading>
- <entry>  (the degree plus at most the two courses most relevant to this job)
## <Languages heading>
<one line, e.g. "English (native) · Spanish (fluent) · German (intermediate)">
It must fit on one A4 page."""

COVER_SYSTEM = f"""You write a short cover letter for a software engineer applying to a specific job.
{RULES}
Format: plain text, no subject line, no placeholders, 90-160 words. Greeting, 2 short paragraphs
linking the candidate's real experience to what this job needs, a one-line close, and the
candidate's name. Honest and specific, no hype; do not claim experience the profile lacks."""

LANG_NAMES = {"en": "English", "ru": "Russian"}


def detect_lang(text: str) -> str:
    cyr, lat = len(CYRILLIC_RE.findall(text)), len(LATIN_RE.findall(text))
    return "ru" if cyr > lat else "en"


def slug(s: str, limit: int = 40) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-")[:limit] or "x"


def job_brief(job: dict) -> str:
    desc = (job.get("descriptionText") or "")[:MAX_DESCRIPTION_CHARS]
    return (f"Title: {job.get('title')}\nCompany: {job.get('companyName')}\n"
            f"Location: {job.get('location')}\n\nDescription:\n{desc}")


def invented_numbers(text: str, profile: str, job: dict) -> list[str]:
    """Numbers in text that aren't in profile.md — or in the job's own title/company, which the
    cover letter may name ("ИТ-Холдинг Т1")."""
    allowed = set(NUMBER_RE.findall(f"{profile} {job.get('title')} {job.get('companyName')}"))
    return sorted({n for n in NUMBER_RE.findall(text) if n not in allowed})


def generate(system: str, profile: str, job: dict, lang: str) -> str:
    prompt = (f"Write it in {LANG_NAMES[lang]}.\n\n<candidate_profile>\n{profile}\n</candidate_profile>\n\n"
              f"<job_posting>\n{job_brief(job)}\n</job_posting>")
    text = backend.call_llm(prompt, system, model=TAILOR_MODEL, timeout=300)
    bad = invented_numbers(text, profile, job)
    if bad:
        retry = (prompt + f"\n\nYour previous draft contained numbers that are not in the profile: "
                 f"{', '.join(bad)}. Rewrite it without them.\n\n<previous_draft>\n{text}\n</previous_draft>")
        text = backend.call_llm(retry, system, model=TAILOR_MODEL, timeout=300)
        bad = invented_numbers(text, profile, job)
        if bad:
            raise SystemExit(f"Refusing to save: output still contains numbers not in profile.md: {', '.join(bad)}")
    return strip_dashes(re.sub(r"^```\w*\n|\n```$", "", text.strip()))


def strip_dashes(text: str) -> str:
    """The user's rule: no em dash or "--" anywhere in a CV or cover letter, a space instead.
    Date-range dashes ("2019 – 2025") stay. Runs on every output, whatever the model wrote."""
    text = re.sub(r"[ \t]*(?:—|--)[ \t]*", " ", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', s)


def markdown_to_html(md: str) -> str:
    out, in_list = [], False
    for line in md.splitlines():
        line = line.rstrip()
        is_item = line.startswith(("- ", "* "))
        if in_list and not is_item:
            out.append("</ul>")
            in_list = False
        if not line:
            continue
        if is_item:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(line[2:])}</li>")
        elif line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        else:
            out.append(f"<p>{inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


PDF_CSS = """
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; font-size: 10pt;
       line-height: 1.32; color: #1a1a1a; }
h1 { font-size: 20pt; margin: 0 0 2pt; }
h1 + p { color: #444; margin: 0 0 8pt; }
h2 { font-size: 11.5pt; text-transform: uppercase; letter-spacing: .06em; color: #1f4e79;
     border-bottom: 1px solid #c9d6e3; padding-bottom: 2pt; margin: 9pt 0 4pt; }
h3 { font-size: 10pt; margin: 6pt 0 2pt; }
p { margin: 0 0 4pt; }
ul { margin: 0 0 4pt; padding-left: 14pt; }
li { margin: 0 0 1.5pt; }
a { color: inherit; text-decoration: none; }
"""


def render_pdf(md: str, pdf_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    doc = f"<!doctype html><html><head><meta charset='utf-8'><style>{PDF_CSS}</style></head><body>{markdown_to_html(md)}</body></html>"
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME_PATH, headless=True)
        try:
            page = browser.new_page()
            page.set_content(doc, wait_until="load")
            page.pdf(path=str(pdf_path), format="A4", print_background=True,
                     margin={"top": "12mm", "bottom": "12mm", "left": "14mm", "right": "14mm"})
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs-file", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--pdf", action="store_true", help="Also render the CV to PDF")
    parser.add_argument("--cover-letter", action="store_true", help="Also write <stem>_cover.txt")
    parser.add_argument("--lang", choices=("auto", "en", "ru"), default="auto")
    args = parser.parse_args()

    job = select_job(load_jobs(args.jobs_file), args.job_id)
    profile = args.profile.read_text(encoding="utf-8")
    lang = detect_lang(job_brief(job)) if args.lang == "auto" else args.lang
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.output_dir / f"{slug(job.get('companyName'))}_{slug(job.get('title'))}_{args.job_id}"

    print(f"Tailoring CV for {job.get('companyName')} — {job.get('title')} ({LANG_NAMES[lang]}, {TAILOR_MODEL})…")
    cv = generate(CV_SYSTEM, profile, job, lang)
    stem.with_suffix(".md").write_text(cv + "\n", encoding="utf-8")
    print(f"CV: {stem.with_suffix('.md')}")
    if args.pdf:
        render_pdf(cv, stem.with_suffix(".pdf"))
        print(f"PDF: {stem.with_suffix('.pdf')}")
    if args.cover_letter:
        cover = generate(COVER_SYSTEM, profile, job, lang)
        cover_path = Path(f"{stem}_cover.txt")
        cover_path.write_text(cover + "\n", encoding="utf-8")
        print(f"Cover letter: {cover_path}")


if __name__ == "__main__":
    main()
