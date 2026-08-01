#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""build_ipol_qs.py -- generate QuickStatements for the IPOL articles pass, as a
fallback for an OpenRefine uploader bug (NullPointerException in LaxValueMatcher).

Reads the OpenRefine project export (which carries the software QIDs written back
from pass 1 in `sw_qid`) and emits QS V1 commands.

NEW articles (article_status=new): CREATE the article with the full bibliographic
core (Crossref reference on each statement), then fold BOTH cross-links using the
just-created item via LAST -- `LAST P921 <sw_qid>` (article -> software) and
`<sw_qid> P1343 LAST` (software -> article) -- so no post-hoc article-QID
resolution is needed for the new rows. Rows sharing one DOI (the cm_fds split)
produce ONE CREATE with two P921/P1343 (shared article, two software). The IPOL
reference (venue Q50815456 + DOI URL) sits on P921/P1343; Crossref on the rest.

EXISTING articles (article_status=existing): pass the export WITHOUT the new-only
facet; those rows carry `existing_article_qid`, and only the two cross-links are
emitted (both QIDs already known).

Usage: python build_ipol_qs.py <export.csv> <out.txt> [--status new|existing|all]
  --status new       only CREATE new articles + their folded cross-links (default
                     when the export is the new-only facet)
  --status existing  only the P921/P1343 cross-links for pre-existing articles
                     (both QIDs known; no CREATE) -- run on the FULL project export
  --status all       both (only safe if nothing has been uploaded yet)
"""
import csv
import sys
from collections import OrderedDict

VENUE_QID = "Q50815456"      # Image Processing On Line (P1433)
ART_CLASS = "Q18918145"      # academic journal article
CROSSREF = "Q5188229"
ENGLISH = "Q1860"
RETRIEVED = "+2026-06-18T00:00:00Z/11"


def q(s):
    """QS-quote a string value."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def crossref_ref(r):
    return ["S248", CROSSREF, "S356", q(r["article_doi_upper"]),
            "S854", q(r["crossref_ref_url"]), "S813", RETRIEVED]


def ipol_ref(r):
    return ["S248", VENUE_QID, "S854", q(r["repo_ref_url_P854"]), "S813", RETRIEVED]


def date_qs(d):
    return "+" + d.strip() + "T00:00:00Z/11"


def line(*cells):
    return "\t".join(cells)


def main():
    src, out = sys.argv[1], sys.argv[2]
    status_filter = "all"
    if "--status" in sys.argv:
        status_filter = sys.argv[sys.argv.index("--status") + 1]
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig")))
    if status_filter != "all":
        rows = [r for r in rows if r["article_status"] == status_filter]

    # group by article DOI, preserving order (cm_fds -> one article, two software)
    by_doi = OrderedDict()
    for r in rows:
        by_doi.setdefault(r["article_doi"], []).append(r)

    L = []
    n_new = n_existing = n_p921 = n_p1343 = 0
    for doi, group in by_doi.items():
        r0 = group[0]
        status = r0["article_status"]
        if status == "new":
            n_new += 1
            L.append("CREATE")
            art = "LAST"
            L.append(line(art, "Len", q(r0["article_title"])))
            L.append(line(art, "Den", q(r0["article_desc"])))
            L.append(line(art, "P31", ART_CLASS, *crossref_ref(r0)))
            L.append(line(art, "P356", q(r0["article_doi_upper"]), *crossref_ref(r0)))
            L.append(line(art, "P1476", "en:" + q(r0["article_title"]), *crossref_ref(r0)))
            L.append(line(art, "P1433", VENUE_QID, *crossref_ref(r0)))
            L.append(line(art, "P577", date_qs(r0["publication_date_P577"]), *crossref_ref(r0)))
            L.append(line(art, "P407", ENGLISH, *crossref_ref(r0)))
        else:
            n_existing += 1
            art = r0["existing_article_qid"].strip()
        # cross-links: one pair per row (per software) of this article.
        # NOTE (learned the hard way): the current QuickStatements backend resolves
        # LAST only as a statement SUBJECT, not as a VALUE. So for NEW articles the
        # `LAST P921 <sw>` fold works, but `<sw> P1343 LAST` fails (422
        # patch-result-invalid-value). We therefore emit P1343 for new articles only
        # when the article QID is explicit (existing rows); for the new rows, run the
        # P1343 completion as a follow-up batch from the run report (which supplies
        # each new article's QID). Also, once a line uses an explicit subject the LAST
        # context is lost, so multiple P921 folds per article (the cm_fds split) break
        # after the first; those too are completed from the report.
        explicit = status == "existing"
        for r in group:
            sw = r["sw_qid"].strip()
            L.append(line(art, "P921", sw, *ipol_ref(r)))       # article -> software
            n_p921 += 1
            if explicit:
                L.append(line(sw, "P1343", art, *ipol_ref(r)))  # software -> article
                n_p1343 += 1

    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")

    print(f"wrote {out}")
    print(f"  distinct articles: {len(by_doi)}  (new CREATE: {n_new}, existing: {n_existing})")
    print(f"  P921 (article->software): {n_p921}   P1343 (software->article): {n_p1343}")
    print(f"  total QS lines: {len(L)}")


if __name__ == "__main__":
    main()
