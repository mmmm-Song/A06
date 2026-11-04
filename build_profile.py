#!/usr/bin/env python3
"""Suggest a local editable profile from a résumé. Does not send data online."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILLS = ["sap", "excel", "power bi", "financial analysis", "financial reporting", "accounting", "banking", "sql", "accounts payable", "accounts receivable", "reconciliation", "fabric", "forecast", "python", "java", "tableau", "power query", "microsoft office", "budgeting", "risk management"]
ALIASES = {"power bi": ["powerbi"], "power query": ["power queries"], "excel": ["microsoft excel"]}


def extract(path):
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF support requires pypdf: python3 -m pip install pypdf") from exc
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    raise ValueError("Use a .pdf or UTF-8 .txt résumé")


def suggest(text, template):
    normalized = re.sub(r"\s+", " ", text.casefold())
    skills = [skill for skill in SKILLS if any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", normalized) for term in [skill] + ALIASES.get(skill, []))]
    profile = dict(template)
    profile["skills"] = skills
    # Preferences and domains cannot be reliably inferred from a résumé.
    profile["domain_terms"] = [term for term in ("finance", "financial", "accounting", "banking", "data analysis", "business intelligence", "software", "marketing", "engineering") if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", normalized)]
    profile["preferred_locations"] = []
    profile["excluded_title_terms"] = []
    profile["maximum_years_experience"] = None
    profile["require_us_sponsorship"] = False
    return profile


def main():
    parser = argparse.ArgumentParser(description="Create a draft local search profile from a résumé")
    parser.add_argument("resume", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "profile.json")
    args = parser.parse_args()
    template = json.loads((ROOT / "profile.example.json").read_text(encoding="utf-8"))
    text = extract(args.resume)
    if not text.strip():
        raise ValueError("No text found. Scanned PDFs need OCR or use a text file.")
    profile = suggest(text, template)
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("Draft created at {}. Review skills and domain terms, and add preferences and exclusions before running the digest.".format(args.output))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc))
