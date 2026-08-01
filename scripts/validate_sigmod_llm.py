#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""
validate_sigmod_llm.py -- LLM-based cross-validation of regex-extracted repo URLs.

For each SIGMOD reproducibility report PDF cached locally, extract the PDF text
and ask Claude to identify the primary code repository. Compare with the URL
already extracted by harvest_sigmod.py (regex + annotation links).

Disagreements are flagged for manual review; agreement rate is reported as a
proxy for extraction quality.

LLM responses are cached in cache/llm_cache.json to avoid redundant API calls.

Usage:
  python validate_sigmod_llm.py
  python validate_sigmod_llm.py --model claude-sonnet-4-6 --limit 10
  python validate_sigmod_llm.py --refresh   # ignore cached LLM responses

Requires: ANTHROPIC_API_KEY environment variable
Output:   sigmod_llm_validation.csv
"""
import os, re, csv, sys, json, time, hashlib, logging, argparse, urllib.parse
import pypdf
import anthropic

HERE     = os.path.dirname(os.path.abspath(__file__))
PDF_DIR  = os.path.join(HERE, "cache", "sigmod_pdfs")
LLM_CACHE= os.path.join(HERE, "cache", "llm_cache.json")
PAIRS    = os.path.join(HERE, "sigmod_pairs.csv")
OUT      = os.path.join(HERE, "sigmod_llm_validation.csv")

DEFAULT_MODEL  = "claude-haiku-4-5-20251001"
MAX_TEXT_CHARS = 10_000   # ~2 500 tokens; enough for abstract + artifact section

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pdf_path(url: str) -> str:
    return os.path.join(PDF_DIR, hashlib.md5(url.encode()).hexdigest() + ".pdf")

def extract_text(pdf_path: str) -> str:
    try:
        reader = pypdf.PdfReader(pdf_path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)[:MAX_TEXT_CHARS]
    except Exception as e:
        logging.warning(f"PDF read error {pdf_path}: {e}")
        return ""

def canon(url: str) -> str:
    """Lowercase org/repo segment for case-insensitive comparison."""
    if not url or url.lower() == "none":
        return ""
    url = url.strip().rstrip(".,;:()")
    try:
        p = urllib.parse.urlparse(url)
        netloc = p.netloc.lower()
        for host in ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org", "zenodo.org"):
            if host in netloc:
                parts = [x for x in p.path.split("/") if x]
                if len(parts) >= 2:
                    return f"https://{netloc}/{parts[0].lower()}/{parts[1].lower()}"
    except Exception:
        pass
    return url.lower()

# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

_PROMPT = """\
You are reviewing a SIGMOD reproducibility report. Identify the PRIMARY code \
repository where the artifact for this paper can be found.

Rules:
- Look for GitHub / GitLab / Bitbucket / Zenodo / Codeberg URLs.
- Prefer the paper's own artifact repository over cited third-party tools or \
baselines.
- Ignore DOI, arXiv, and documentation-only links.
- If multiple repos are mentioned, pick the one most likely to be the authors' \
own artifact (keyword signals: "artifact", "reproducib", "experiment", "sigmod", \
"our code", "our implementation").

Return ONLY the canonical URL (e.g. https://github.com/user/repo).
If no repository is mentioned, return exactly: none

Report text:
{text}"""

def ask_llm(client: anthropic.Anthropic, text: str, model: str,
            llm_cache: dict, cache_key: str, refresh: bool) -> str:
    if not refresh and cache_key in llm_cache:
        return llm_cache[cache_key]
    time.sleep(0.3)
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=128,
            messages=[{"role": "user", "content": _PROMPT.format(text=text)}],
        )
        result = msg.content[0].text.strip()
    except Exception as e:
        logging.error(f"LLM call failed: {e}")
        result = "error"
    llm_cache[cache_key] = result
    return result

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model",   default=DEFAULT_MODEL,
                    help=f"Claude model to use (default: {DEFAULT_MODEL})")
    ap.add_argument("--limit",   type=int, default=None, metavar="N",
                    help="Process only the first N papers (smoke-test)")
    ap.add_argument("--refresh", action="store_true",
                    help="Ignore cached LLM responses and re-call the API")
    args = ap.parse_args()

    client = anthropic.Anthropic()

    llm_cache: dict = {}
    if os.path.exists(LLM_CACHE) and not args.refresh:
        with open(LLM_CACHE, encoding="utf-8") as f:
            llm_cache = json.load(f)

    rows  = list(csv.DictReader(open(PAIRS, encoding="utf-8")))
    results = []
    n_match = n_diff = n_both_empty = n_skipped = 0

    for row in rows:
        if args.limit and len(results) >= args.limit:
            break

        report_url = row.get("report_url", "").strip()
        if not report_url or "proceedings" in report_url:
            continue

        pdf_path = _pdf_path(report_url)
        if not os.path.exists(pdf_path):
            n_skipped += 1
            continue

        text = extract_text(pdf_path)
        if not text:
            n_skipped += 1
            continue

        regex_url = row.get("repo_url", "").strip()
        cache_key = hashlib.md5((report_url + args.model).encode()).hexdigest()
        llm_url   = ask_llm(client, text, args.model, llm_cache, cache_key, args.refresh)

        c_regex = canon(regex_url)
        c_llm   = canon(llm_url)

        if not c_regex and not c_llm:
            verdict = "both_empty"
            n_both_empty += 1
        elif c_regex == c_llm:
            verdict = "match"
            n_match += 1
        else:
            verdict = "diff"
            n_diff += 1

        tag = {"match": "OK  ", "both_empty": "OK  ", "diff": "DIFF"}[verdict]
        logging.info(f"{tag} {row['doi']}")
        if verdict == "diff":
            logging.info(f"     regex: {regex_url or '(empty)'}")
            logging.info(f"     llm:   {llm_url}")

        results.append({
            "doi":       row["doi"],
            "year":      row["year"],
            "title":     row["title"][:80],
            "regex_url": regex_url,
            "llm_url":   llm_url,
            "verdict":   verdict,
        })

    # Persist LLM cache
    with open(LLM_CACHE, "w", encoding="utf-8") as f:
        json.dump(llm_cache, f, indent=2)

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["doi","year","title","regex_url","llm_url","verdict"])
        w.writeheader()
        w.writerows(results)

    total = len(results)
    agree = n_match + n_both_empty
    logging.info(f"\n=== Validation done: {total} PDFs processed, {n_skipped} skipped ===")
    logging.info(f"    Match:      {n_match}")
    logging.info(f"    Both empty: {n_both_empty}")
    logging.info(f"    Diff:       {n_diff}")
    logging.info(f"    Agreement:  {agree}/{total} ({agree/total*100:.1f}%)" if total else "")
    logging.info(f"    Output: {OUT}")

if __name__ == "__main__":
    main()
