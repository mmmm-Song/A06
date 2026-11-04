#!/usr/bin/env python3
"""Find recent jobs using public employer APIs. Never submits applications."""
import argparse
import datetime as dt
import html
import json
import re
import sys
import urllib.error
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
            yield {"title": item.get("title", ""), "company": board, "url": item.get("absolute_url", ""),
                   "location": (item.get("location") or {}).get("name", ""), "description": clean(item.get("content", "")),
                   "posted": parse_date(item.get("first_published")), "source": "Greenhouse"}
    else:
        raise ValueError("unsupported source")


def evaluate(job, now):
    if not job["posted"] or not now - dt.timedelta(hours=48) <= job["posted"] <= now:
        return None
    if not job["url"].startswith(("https://boards.greenhouse.io/", "https://job-boards.greenhouse.io/", "https://jobs.lever.co/")):
        return None
    text = (job["title"] + " " + job["description"]).lower()
    loc = job["location"].lower()
    # Avoid treating a mention of SAP in an unrelated job as a finance job.
    domains = ["finance", "financial", "accounting", "accountant", "banking", "credit analysis", "comptab", "financier", "data analy", "analyse de données", "business intelligence"]
    if not any(term in text for term in domains):
        return None
    if re.search(r"\b(customer service|client service|customer support|account executive|sales|business development|data scientist|conseiller service à la clientèle)\b", job["title"], re.I):
        return None
    if re.search(r"\b(?:[6-9]|1[0-9])\s*\+?\s*(?:years|ans)\s+(?:of |d['’])?(?:relevant |professional )?(?:experience|expérience)", text):
        return None
    no_sponsor = re.search(r"(?:no|without|not (?:provide|offer)|unable to (?:provide|offer))[^.\n]{0,60}sponsor|sponsor[^.\n]{0,60}(?:not available|not provided|will not)", text)
    us = bool(re.search(r"\b(united states|usa|u\.s\.|new york|california|texas|san francisco|seattle|chicago|menlo park|\bny\b|\bca\b)\b", loc))
    if us and no_sponsor:
        return None
    terms = ["sap", "excel", "power bi", "financial analysis", "financial reporting", "accounting", "banking", "sql", "accounts payable", "accounts receivable", "reconciliation", "fabric", "forecast"]
    hits = [term for term in terms if term in text]
    if len(hits) < 2:
        return None
    score = len(hits) + (5 if re.search(r"montr[eé]al", loc) else 0) + (2 if re.search(r"canada|quebec|qu[eé]bec", loc) else 0)
    job.update(score=score, hits=hits, sponsorship="Check posting; not confirmed" if us else "Not assessed", us=us)
    return job


def collect(config, now):
    results, errors, seen = [], [], set()
    for source in config["sources"]:
        try:
            for job in jobs_for(source):
                match = evaluate(job, now)
                if match and match["url"] not in seen and listing_available(match):
                    results.append(match)
                    seen.add(match["url"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append("{} / {}: {}".format(source.get("type"), source.get("board"), exc))
    results.sort(key=lambda x: (-x["score"], -x["posted"].timestamp()))
    return results[:min(20, max(1, int(config.get("max_results", 20))))], errors


def listing_available(job):
    """Check the individual hosted listing in addition to the active jobs API."""
    try:
        request = urllib.request.Request(job["url"], headers={"User-Agent": HEADERS["User-Agent"]})
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(500000).decode("utf-8", "replace").lower()
            return response.status == 200 and not any(phrase in body for phrase in (
                "this job is no longer available", "this job is no longer accepting applications",
                "this position is no longer available", "job not found")) and job["title"].lower() in html.unescape(body)
    except (OSError, ValueError):
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
        lines += ["Sources unavailable (results may be incomplete):"] + errors
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
    now = dt.datetime.now(UTC)
    results, errors = collect(config, now)
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
