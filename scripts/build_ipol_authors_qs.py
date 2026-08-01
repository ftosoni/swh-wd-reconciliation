#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""build_ipol_authors_qs.py -- QuickStatements for the IPOL authors pass.

P2093 (author name string) + P1545 (series ordinal) + Crossref reference, on NEW
articles only (existing WikiCite articles already carry their authors). Article
QIDs come from the QS run report of the article-creation batch (DOI->QID), since
QuickStatements does not write QIDs back into the OpenRefine project.

Usage: python build_ipol_authors_qs.py <batch_report.csv> <out.txt>
"""
import csv
import sys

RET = "+2026-06-18T00:00:00Z/11"
CROSSREF = "Q5188229"
AUTHORS = "import/authors/IPOL.csv"


def q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main():
    report, out = sys.argv[1], sys.argv[2]

    # DOI(upper) -> new article QID, from the P356 rows of the run report
    doi2art = {}
    for r in csv.DictReader(open(report, encoding="utf-8-sig")):
        p = r["raw_input"].split("|")
        if len(p) > 2 and p[1] == "P356" and r["entity_id"]:
            doi2art[p[2].strip('"').upper()] = r["entity_id"]

    lines, seen, arts = [], set(), set()
    n_skip = set()
    for a in csv.DictReader(open(AUTHORS, encoding="utf-8-sig")):
        doi = a["article_doi"].strip()
        art = doi2art.get(doi.upper())
        if not art:                       # existing article -> skip (authors already there)
            n_skip.add(doi)
            continue
        key = (art, a["series_ordinal_P1545"], a["author_name_P2093"])
        if key in seen:
            continue
        seen.add(key)
        arts.add(art)
        url = "https://api.crossref.org/works/" + doi
        lines.append("\t".join([
            art, "P2093", q(a["author_name_P2093"]),
            "P1545", q(a["series_ordinal_P1545"]),
            "S248", CROSSREF, "S854", q(url), "S813", RET]))

    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    print(f"wrote {out}")
    print(f"  author statements: {len(lines)}")
    print(f"  new articles covered: {len(arts)}")
    print(f"  DOIs skipped (existing / not new): {len(n_skip)}")


if __name__ == "__main__":
    main()
