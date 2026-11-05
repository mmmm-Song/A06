#!/usr/bin/env python3
"""Find recent jobs using public employer APIs. Never submits applications."""
import argparse
import datetime as dt
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo
import outlook

ROOT = Path(__file__).resolve().parent
UTC = dt.timezone.utc
HEADERS = {"User-Agent": "JobSearchDigest/1.0 (personal job search)", "Accept": "application/json"}


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=20) as response:
        return json.load(response)


def clean(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", text or ""))).strip()


def parse_date(value):
    try:
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(value / 1000, UTC)
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (TypeError, ValueError, OverflowError, AttributeError):
        return None


def jobs_for(source):
    board = source["board"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", board):
        raise ValueError("invalid board name")
    if source["type"] == "lever":
        data = fetch("https://api.lever.co/v0/postings/" + board + "?mode=json")
        for item in data:
            categories = item.get("categories") or {}
            yield {"title": item.get("text", ""), "company": board, "url": item.get("hostedUrl", ""),
                   "location": categories.get("location", ""), "description": clean(item.get("descriptionPlain", "") + " " + item.get("additionalPlain", "") + " " + " ".join(clean(x.get("content", "")) for x in item.get("lists", []))),
                   "posted": parse_date(item.get("createdAt")), "source": "Lever"}
    elif source["type"] == "greenhouse":
        data = fetch("https://boards-api.greenhouse.io/v1/boards/" + board + "/jobs?content=true")
        for item in data.get("jobs", []):
            # updated_at is not a publication date; never use it as one.
            job_id = item.get("id")
            if not isinstance(job_id, int):
                continue
            yield {"title": item.get("title", ""), "company": board, "url": "https://job-boards.greenhouse.io/{}/jobs/{}".format(board, job_id),
                   "company_url": item.get("absolute_url", ""), "job_id": str(job_id),
                   "location": (item.get("location") or {}).get("name", ""), "description": clean(item.get("content", "")),
                   "posted": parse_date(item.get("first_published")), "source": "Greenhouse"}
    else:
        raise ValueError("unsupported source")


def evaluate(job, now, profile=None):
    if profile is None:
        profile = json.loads((ROOT / "profile.example.json").read_text(encoding="utf-8"))
    if not job["posted"] or not now - dt.timedelta(hours=48) <= job["posted"] <= now:
        return None
    if not job["url"].startswith(("https://boards.greenhouse.io/", "https://job-boards.greenhouse.io/", "https://jobs.lever.co/")):
        return None
    text = (job["title"] + " " + job["description"]).lower()
    loc = job["location"].lower()
    # Avoid treating a mention of SAP in an unrelated job as a finance job.
    domains = profile["domain_terms"]
    if not any(term.lower() in text for term in domains):
        return None
    if any(term.lower() in job["title"].lower() for term in profile["excluded_title_terms"] if term.strip()):
        return None
    maximum = profile.get("maximum_years_experience")
    if maximum is not None:
        years = re.findall(r"\b(\d{1,2})\s*\+?\s*(?:years|ans)\s+(?:of |d['’])?(?:relevant |professional )?(?:experience|expérience)", text)
        if any(int(value) > maximum for value in years):
            return None
    no_sponsor = re.search(r"(?:no|without|not (?:provide|offer)|unable to (?:provide|offer))[^.\n]{0,60}sponsor|sponsor[^.\n]{0,60}(?:not available|not provided|will not)", text)
    us = bool(re.search(r"\b(united states|usa|u\.s\.|new york|california|texas|san francisco|seattle|chicago|menlo park|ny|ca)\b", loc)) and not bool(re.search(r"canada|quebec|québec|montr[eé]al", loc))
    if us and no_sponsor and profile.get("require_us_sponsorship", False):
        return None
    hits = [term for term in profile["skills"] if term.strip() and term.lower() in text]
    if len(hits) < profile.get("minimum_skill_matches", 2):
        return None
    score = len(hits) + (5 if any(place.lower() in loc for place in profile["preferred_locations"] if place.strip()) else 0)
    job.update(score=score, hits=hits, sponsorship="Check posting; not confirmed" if us else "Not assessed", us=us)
    return job


def collect(config, now, profile=None):
    results, errors, seen = [], [], set()
    unverified = 0
    for source in config["sources"]:
        try:
            for job in jobs_for(source):
                match = evaluate(job, now, profile)
                if match and match["url"] not in seen:
                    original_url = match["url"]
                    if listing_available(match):
                        results.append(match)
                        seen.add(original_url)
                    else:
                        unverified += 1
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append("{} / {}: {}".format(source.get("type"), source.get("board"), exc))
    if unverified:
        errors.append("{} potential matches excluded because their individual pages could not be verified.".format(unverified))
    results.sort(key=lambda x: (-x["score"], -x["posted"].timestamp()))
    return results[:min(20, max(1, int(config.get("max_results", 20))))], errors


def listing_available(job):
    """Check the individual hosted listing in addition to the active jobs API."""
    urls = [job["url"]]
    company_url = job.get("company_url", "")
    if company_url.startswith("http://"):
        company_url = "https://" + company_url[len("http://"):]
    parts = urllib.parse.urlsplit(company_url)
    if parts.scheme == "https" and parts.hostname and job.get("job_id") and re.search(r"(?<!\d)" + re.escape(job["job_id"]) + r"(?!\d)", parts.path):
        urls.append(company_url)
    for url in dict.fromkeys(urls):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; JobSearchDigest/1.0)"})
            with urllib.request.urlopen(request, timeout=15) as response:
                body = html.unescape(response.read(500000).decode("utf-8", "replace")).lower()
                if not response.url.startswith("https://"):
                    continue
                if response.status == 200 and not any(phrase in body for phrase in (
                    "this job is no longer available", "this job is no longer accepting applications",
                    "this position is no longer available", "job not found")) and job["title"].lower() in body:
                    job["url"] = response.url
                    return True
        except (OSError, ValueError):
            continue
    return False


def render(results, errors, now):
    lines = ["Daily job digest — " + now.astimezone(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d %H:%M %Z"),
             "Jobs published within the previous 48 hours; checked against employer job-board APIs at search time.",
             "A listing can close after this check. Verify requirements and availability before applying.", ""]
    for i, job in enumerate(results, 1):
        lines += ["{}. {} — {}".format(i, job["title"], job["company"]),
                  "Location: {} | Posted: {} | Source: {}".format(job["location"] or "Not specified", job["posted"].strftime("%Y-%m-%d %H:%M UTC"), job["source"]),
                  "Matching terms: " + ", ".join(job["hits"]),
                  "US sponsorship: " + job["sponsorship"], job["url"], ""]
    if not results:
        lines += ["No qualifying jobs found in the configured sources.", ""]
    if errors:
        lines += ["Search limitations (results may be incomplete):"] + errors
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--preview", action="store_true", help="print results instead of sending email")
    parser.add_argument("--authorize", action="store_true", help="connect a personal Microsoft account")
    args = parser.parse_args()
    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)
    if args.authorize:
        outlook.authorize(config)
        return
    profile_path = Path(config.get("profile_file", "profile.example.json"))
    if not profile_path.is_absolute():
        profile_path = Path(args.config).resolve().parent / profile_path
    with profile_path.open(encoding="utf-8") as f:
        profile = json.load(f)
    if not profile.get("domain_terms") or not profile.get("skills"):
        raise ValueError("Review your profile and add at least one domain term and skill before searching.")
    now = dt.datetime.now(UTC)
    results, errors = collect(config, now, profile)
    report = render(results, errors, now)
    if args.preview:
        print(report)
        return
    outlook.send(config, "Daily job digest — {} matches".format(len(results)), report)
    print("Sent digest with {} jobs; {} source errors".format(len(results), len(errors)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Digest failed: {}".format(exc), file=sys.stderr)
        sys.exit(1)
