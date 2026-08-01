#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""
build_author_import.py -- Build the *new-articles-only* author-import CSV for a
venue, keyed by the freshly minted article QID.

Context. The author pass (P2093 author name string + P1545 series ordinal, with a
Crossref reference) must run only on articles this project *created*, never on the
pre-existing WikiCite articles: those already carry their authors, so re-adding
P2093 would duplicate authorship and attach a redundant Crossref reference.

The subject of each author statement is the article item. Because the articles
were created minutes earlier, they are not yet in the Wikidata reconciliation
service's search index, so reconciling by DOI would silently miss them. Instead we
reuse the exact QIDs OpenRefine wrote back into the main project after the upload:
export that project (any column layout, as long as it carries `article_doi`,
`article_status`, `art_qid`) and this script joins it onto the per-author table.

Inputs (per venue, defaults under import/authors/):
  * qid export  -- OpenRefine export of the main <venue> project, must contain
                   `article_doi`, `article_status`, `art_qid` (the written-back
                   QID column) and, if available, `crossref_ref_url`.
                   Default: import/authors/export_<venue>.csv
  * authors     -- one row per (paper, author): `article_doi`,
                   `series_ordinal_P1545`, `author_name_P2093`.
                   Default: import/authors/<venue>.csv

Output:
  * import/authors/<venue>_new.csv -- one row per (new article, author) with
    columns art_qid, article_doi, series_ordinal_P1545, author_name_P2093,
    crossref_ref_url. Ready to import: reconcile `art_qid` via
    "Use values as identifiers", then add P2093 (+ P1545 qualifier, Crossref ref).

The join drops existing-article authors, deduplicates identical (article, ordinal,
name) rows -- multi-repo papers emit each author once per repo row in the source
authors table -- and flags the genuine anomaly of one ordinal bearing two
different names.
"""
import argparse
import csv
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
AUTHORS_DIR = os.path.join(HERE, "import", "authors")
QID_RE = re.compile(r"^Q\d+$")
OUT_COLUMNS = ["art_qid", "article_doi", "series_ordinal_P1545",
               "author_name_P2093", "crossref_ref_url"]


def load_new_article_qids(qid_export_path):
    """DOI -> (art_qid, crossref_ref_url) for rows flagged article_status=new."""
    dmap = {}
    with open(qid_export_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for col in ("article_doi", "article_status", "art_qid"):
            if col not in reader.fieldnames:
                sys.exit(f"ERROR: qid export {qid_export_path} lacks column '{col}'")
        for r in reader:
            if r["article_status"].strip() != "new":
                continue
            doi = r["article_doi"].strip()
            qid = r["art_qid"].strip()
            if not QID_RE.match(qid):
                print(f"  WARN new article {doi} has no valid art_qid ({qid!r}); skipped")
                continue
            cru = (r.get("crossref_ref_url") or "").strip()
            if not cru:
                cru = f"https://api.crossref.org/works/{doi}"
            if doi in dmap and dmap[doi][0] != qid:
                sys.exit(f"ERROR: DOI {doi} maps to conflicting QIDs "
                         f"{dmap[doi][0]} vs {qid}")
            dmap[doi] = (qid, cru)
    return dmap


def build(venue, qid_export_path, authors_path, out_path):
    dmap = load_new_article_qids(qid_export_path)
    print(f"  new-article DOIs with a QID: {len(dmap)}")

    seen = set()
    out = []
    names_at = defaultdict(set)  # (qid, ordinal) -> {names}
    skipped_existing = 0
    with open(authors_path, encoding="utf-8-sig") as f:
        for a in csv.DictReader(f):
            doi = a["article_doi"].strip()
            if doi not in dmap:
                skipped_existing += 1
                continue
            qid, cru = dmap[doi]
            ordinal = a["series_ordinal_P1545"].strip()
            name = a["author_name_P2093"].strip()
            names_at[(qid, ordinal)].add(name)
            key = (qid, ordinal, name)
            if key in seen:      # multi-repo papers duplicate each author row
                continue
            seen.add(key)
            out.append({
                "art_qid": qid,
                "article_doi": doi,
                "series_ordinal_P1545": ordinal,
                "author_name_P2093": name,
                "crossref_ref_url": cru,
            })

    conflicts = {k: v for k, v in names_at.items() if len(v) > 1}
    if conflicts:
        print(f"  WARN {len(conflicts)} (article, ordinal) pairs carry different "
              f"names -- inspect before upload:")
        for (qid, ordinal), names in list(conflicts.items())[:10]:
            print(f"    {qid} ordinal {ordinal}: {sorted(names)}")

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLUMNS)
        w.writeheader()
        w.writerows(out)

    print(f"  {venue:10} author rows (new only, deduped)={len(out)} "
          f"articles={len({r['art_qid'] for r in out})} "
          f"skipped(existing/other)={skipped_existing} -> {os.path.relpath(out_path, HERE)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("venue", help="venue name, e.g. SoftwareX (matches the CSV basenames)")
    ap.add_argument("--qid-export", help="OpenRefine main-project export with art_qid "
                    "(default: import/authors/export_<venue>.csv)")
    ap.add_argument("--authors", help="per-author table "
                    "(default: import/authors/<venue>.csv)")
    ap.add_argument("--out", help="output path "
                    "(default: import/authors/<venue>_new.csv)")
    args = ap.parse_args()

    qid_export = args.qid_export or os.path.join(AUTHORS_DIR, f"export_{args.venue}.csv")
    authors = args.authors or os.path.join(AUTHORS_DIR, f"{args.venue}.csv")
    out = args.out or os.path.join(AUTHORS_DIR, f"{args.venue}_new.csv")
    for p in (qid_export, authors):
        if not os.path.exists(p):
            sys.exit(f"ERROR: input not found: {p}")
    build(args.venue, qid_export, authors, out)


if __name__ == "__main__":
    main()
