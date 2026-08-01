#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""
extract_sigmod_text.py -- Persist PDF text and build a review dossier.

For every SIGMOD reproducibility report PDF cached locally, this script:
  1. Extracts the full text and writes it to <md5>.txt next to the PDF,
     so the corpus can be re-processed without re-parsing the PDFs.
  2. Collects every candidate code-hosting URL (canonicalised, with the
     number of times it occurs) using the SAME logic as harvest_sigmod.py,
     so the regex pick can be compared apples-to-apples.
  3. Emits a consolidated dossier (sigmod_review.jsonl) with, per report:
     doi, year, title, regex_url (the pick already in sigmod_pairs.csv),
     the ranked candidate list, and text snippets around every link and
     artifact keyword.

The dossier is meant to be read by a human or an LLM reviewer (Claude)
to validate the heuristic extraction without any API calls.

Usage:
  python extract_sigmod_text.py
Output:
  cache/sigmod_pdfs/<md5>.txt   (one per report)
  sigmod_review.jsonl           (one JSON object per report)
"""
import os, re, csv, sys, json, hashlib, logging, urllib.parse
import pypdf

# Reuse the exact canonicalisation / filtering used by the harvester
from harvest_sigmod import clean_url, CODE_HOST, _PDF_SKIP, _cache_path

HERE    = os.path.dirname(os.path.abspath(__file__))
PDF_DIR = os.path.join(HERE, "cache", "sigmod_pdfs")
PAIRS   = os.path.join(HERE, "sigmod_pairs.csv")
DOSSIER = os.path.join(HERE, "sigmod_review.jsonl")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])

_KEYWORDS = ("repositor", "artifact", "availab", "source code", "our code",
             "our implementation", "github", "gitlab", "zenodo", "bitbucket",
             "codeberg", "reproduc", "http")

_GIT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")


def extract_text(pdf_path: str) -> str:
    reader = pypdf.PdfReader(pdf_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def candidate_urls(pdf_path: str, text: str) -> dict:
    """Return {canonical_url: occurrence_count}, same rules as harvest_sigmod."""
    raw: set[str] = set()
    raw.update(re.findall(r"https?://\S+", text))
    raw.update(re.findall(
        r"(?<!\w)(?:github|gitlab|bitbucket|codeberg|zenodo|sourceforge)"
        r"\.(?:com|org|net)/\S+", text, re.IGNORECASE,
    ))
    try:
        reader = pypdf.PdfReader(pdf_path)
        for page in reader.pages:
            if "/Annots" not in page:
                continue
            for annot in page["/Annots"]:
                obj = annot.get_object()
                if obj.get("/Subtype") == "/Link":
                    a = obj.get("/A")
                    if a:
                        uri = a.get_object().get("/URI")
                        if uri:
                            raw.add(uri)
    except Exception:
        pass

    counts: dict[str, int] = {}
    for u in raw:
        if not CODE_HOST.search(u):
            continue
        if any(s in u.lower() for s in _PDF_SKIP):
            continue
        canon = clean_url(u)
        p = urllib.parse.urlparse(canon)
        parts = [x for x in p.path.split("/") if x]
        if any(h in p.netloc for h in _GIT_HOSTS) and len(parts) < 2:
            continue  # bare org URL — not a repo
        counts[canon] = counts.get(canon, 0) + 1
    return counts


def snippets(text: str, max_lines: int = 40) -> list:
    """Lines mentioning a link or artifact keyword, with light dedup."""
    out, seen = [], set()
    for line in text.splitlines():
        s = line.strip()
        if len(s) < 4:
            continue
        low = s.lower()
        if any(k in low for k in _KEYWORDS):
            key = re.sub(r"\s+", " ", low)[:120]
            if key not in seen:
                seen.add(key)
                out.append(s[:300])
        if len(out) >= max_lines:
            break
    return out


def main():
    rows = list(csv.DictReader(open(PAIRS, encoding="utf-8")))
    dossier = []
    n_txt = n_skip = 0

    for row in rows:
        report_url = row.get("report_url", "").strip()
        if not report_url or "proceedings" in report_url:
            continue
        pdf_path = _cache_path(report_url, "pdf", "sigmod_pdfs")
        if not os.path.exists(pdf_path):
            n_skip += 1
            continue

        try:
            text = extract_text(pdf_path)
        except Exception as e:
            logging.warning(f"PDF read error {row['doi']}: {e}")
            n_skip += 1
            continue

        # 1. Persist the text
        txt_path = os.path.splitext(pdf_path)[0] + ".txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text)
        n_txt += 1

        # 2. Candidates + 3. dossier entry
        cands = candidate_urls(pdf_path, text)
        ranked = sorted(cands.items(), key=lambda kv: (-kv[1], kv[0]))
        dossier.append({
            "doi":        row["doi"],
            "year":       row["year"],
            "title":      row["title"],
            "regex_url":  row.get("repo_url", "").strip(),
            "candidates": [{"url": u, "count": c} for u, c in ranked],
            "txt_file":   os.path.basename(txt_path),
            "snippets":   snippets(text),
        })

    with open(DOSSIER, "w", encoding="utf-8") as f:
        for entry in dossier:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logging.info(f"Wrote {n_txt} .txt files, skipped {n_skip}")
    logging.info(f"Dossier: {DOSSIER}  ({len(dossier)} reports)")
    # quick distribution of candidate counts
    from collections import Counter
    dist = Counter(len(e["candidates"]) for e in dossier)
    logging.info("Candidate-count distribution (n_candidates: n_reports): "
                 + ", ".join(f"{k}:{v}" for k, v in sorted(dist.items())))


if __name__ == "__main__":
    main()
