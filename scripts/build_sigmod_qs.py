#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""build_sigmod_qs.py -- generate QuickStatements V1 for the SIGMOD import.

The OpenRefine Wikidata uploader is broken (NullPointerException in LaxValueMatcher),
so the whole SIGMOD import is done via QuickStatements generated directly from the
prep CSV (import/checked/SIGMOD.prep.csv). SIGMOD has 0 pre-existing software and no
shared repos / shared articles, so every row is one distinct software + one distinct
article -- the simplest structure of all venues.

Phases (run in order; each later phase reads the run report of the previous one):

  software  CREATE all 78 new software items (P31 Q7397 + P1324[+P8423/P10627] +
            P6138). Reference on P31/P1324 = the SIGMOD Availability & Reproducibility
            report (P854 = the report URL, P813 retrieved; no P248 -- the ARI has no
            Wikidata item and ACM-the-society is a type mismatch for "stated in");
            P6138 gets the Software Heritage reference. No cross-links yet. Needs NO
            report. -> run report maps repo_url/SWHID -> sw QID.

  articles  --sw-report REPORT. New articles (article_status=new): CREATE with the
            bibliographic core (per-row P31 class: PACMMOD -> journal article
            Q18918145, conference volumes -> conference paper Q23927052; P1433 only
            when the proceedings has a Wikidata item, i.e. PACMMOD Q130602410;
            P123 publisher = ACM Q127992, the sole venue/publisher signal for the
            item-less conference volumes) and
            fold P921 via `LAST P921 <sw_qid>` (sw QID from --sw-report). Existing
            articles (status=existing): both cross-links with explicit QIDs
            (existing_article_qid + sw_qid). Cross-link reference = the ARI report URL.
            -> run report maps DOI -> new article QID.

  p1343     --sw-report R1 --art-report R2. The new articles' `<sw_qid> P1343 <art>`
            back-link, with EXPLICIT article QIDs from --art-report (the QS backend
            resolves LAST only as a SUBJECT, never as a VALUE, so this cannot be
            folded in the articles phase).

Usage:
  python build_sigmod_qs.py software  import/sigmod_qs_1_software.txt
  python build_sigmod_qs.py articles  import/sigmod_qs_2_articles.txt --sw-report <r1>
  python build_sigmod_qs.py p1343     import/sigmod_qs_3_p1343.txt --sw-report <r1> --art-report <r2>
"""
import argparse
import csv
import os
import sys
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
PREP = os.path.join(HERE, "import", "checked", "SIGMOD.prep.csv")

RET = "+2026-06-18T00:00:00Z/11"
CROSSREF = "Q5188229"        # Crossref (bibliographic reference)
SWH = "Q28127082"            # Software Heritage (P6138 provenance)
ACM = "Q127992"              # Association for Computing Machinery (article publisher P123)
GIT = "Q186055"              # Git (P8423 version control system)
SOFTWARE = "Q7397"           # software (P31)
ENGLISH = "Q1860"            # English (P407)


def q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def line(*cells):
    return "\t".join(cells)


def date_qs(d):
    return "+" + d.strip() + "T00:00:00Z/11"


def crossref_ref(r):
    return ["S248", CROSSREF, "S356", q(r["article_doi_upper"]),
            "S854", q(r["crossref_ref_url"]), "S813", RET]


def report_ref(r):
    # the SIGMOD Availability & Reproducibility report (col repo_ref_url_P854, 56 on
    # reproducibility.sigmod.org, 22 on dl.acm.org) is where the paper<->repository
    # link is asserted. Reference URL + retrieved only: P248 "stated in" wants a
    # bibliographic source, but the ARI has no Wikidata item and ACM-the-society is a
    # type mismatch, so we cite the report URL directly (a standard reference form).
    return ["S854", q(r["repo_ref_url_P854"]), "S813", RET]


def swh_ref():
    return ["S248", SWH, "S813", RET]


def load_prep():
    return list(csv.DictReader(open(PREP, encoding="utf-8-sig")))


def parse_report(path):
    """QS run report -> list of (raw_input_parts, entity_id). raw_input is the echoed
    command, pipe-separated; entity_id is the resulting/target QID."""
    out = []
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        out.append((r["raw_input"].split("|"), r.get("entity_id", "").strip()))
    return out


def sw_qid_map(report):
    """repo_url -> sw QID (from P1324 rows) and SWHID -> sw QID (from P6138 rows),
    so both the repo rows and the repo-less WeTune row resolve."""
    by_repo, by_swhid = {}, {}
    for parts, ent in parse_report(report):
        if len(parts) > 2 and ent:
            prop, val = parts[1], parts[2].strip().strip('"')
            if prop == "P1324":
                by_repo[val] = ent
            elif prop == "P6138":
                by_swhid[val] = ent
    return by_repo, by_swhid


def resolve_sw(r, by_repo, by_swhid):
    u = r["repo_url_P1324"].strip()
    if u and u in by_repo:
        return by_repo[u]
    s = r["swhid_P6138"].strip()
    if s and s in by_swhid:
        return by_swhid[s]
    return ""


def doi_qid_map(report):
    """DOI(upper) -> new article QID, from the P356 rows of the article run report."""
    m = {}
    for parts, ent in parse_report(report):
        if len(parts) > 2 and parts[1] == "P356" and ent:
            m[parts[2].strip().strip('"').upper()] = ent
    return m


# ---------------------------------------------------------------- phases
def phase_software(rows):
    L, n_repo, n_swhid = [], 0, 0
    for r in rows:
        L.append("CREATE")
        L.append(line("LAST", "Len", q(r["software_label"])))
        if r["software_alias"].strip():
            L.append(line("LAST", "Aen", q(r["software_alias"])))
        L.append(line("LAST", "Den", q(r["software_desc"])))
        # instance of software; evidence = the ARI reproducibility report
        L.append(line("LAST", "P31", SOFTWARE, *report_ref(r)))
        if r["repo_url_P1324"].strip():
            quals = ["P8423", GIT]
            if r["web_interface_qid"].strip():
                quals += ["P10627", r["web_interface_qid"].strip()]
            L.append(line("LAST", "P1324", q(r["repo_url_P1324"]), *quals, *report_ref(r)))
            n_repo += 1
        if r["swhid_P6138"].strip():
            L.append(line("LAST", "P6138", q(r["swhid_P6138"]), *swh_ref()))
            n_swhid += 1
    print(f"  software CREATE: {len(rows)}   with P1324: {n_repo}   with P6138: {n_swhid}")
    return L


def phase_articles(rows, sw_report):
    by_repo, by_swhid = sw_qid_map(sw_report)
    L, n_new, n_exist, n_p921, n_p1343, missing = [], 0, 0, 0, 0, []
    for r in rows:
        sw = resolve_sw(r, by_repo, by_swhid)
        if not sw:
            missing.append(r["article_doi"])
            continue
        if r["article_status"] == "new":
            n_new += 1
            L.append("CREATE")
            art = "LAST"
            L.append(line(art, "Len", q(r["article_title"])))
            L.append(line(art, "Den", q(r["article_desc"])))
            L.append(line(art, "P31", r["article_class_P31"], *crossref_ref(r)))
            L.append(line(art, "P356", q(r["article_doi_upper"]), *crossref_ref(r)))
            L.append(line(art, "P1476", "en:" + q(r["article_title"]), *crossref_ref(r)))
            if r["venue_qid_P1433"].strip():
                L.append(line(art, "P1433", r["venue_qid_P1433"].strip(), *crossref_ref(r)))
            # publisher: ACM (Crossref-sourced). The sole venue/publisher signal for
            # the conference-volume papers, which have no P1433 item.
            L.append(line(art, "P123", ACM, *crossref_ref(r)))
            L.append(line(art, "P577", date_qs(r["publication_date_P577"]), *crossref_ref(r)))
            L.append(line(art, "P407", ENGLISH, *crossref_ref(r)))
            # fold article->software only (LAST works as subject, not as value)
            L.append(line(art, "P921", sw, *report_ref(r)))
            n_p921 += 1
        else:
            n_exist += 1
            art = r["existing_article_qid"].strip()
            L.append(line(art, "P921", sw, *report_ref(r)))
            L.append(line(sw, "P1343", art, *report_ref(r)))
            n_p921 += 1
            n_p1343 += 1
    print(f"  new articles CREATE: {n_new}   existing: {n_exist}")
    print(f"  P921: {n_p921}   P1343 (existing only): {n_p1343}")
    if missing:
        print(f"  !! sw QID unresolved for {len(missing)} rows: {missing[:5]}")
    return L


def phase_p1343(rows, sw_report, art_report):
    by_repo, by_swhid = sw_qid_map(sw_report)
    doi2art = doi_qid_map(art_report)
    L, n, missing = [], 0, []
    for r in rows:
        if r["article_status"] != "new":
            continue
        sw = resolve_sw(r, by_repo, by_swhid)
        art = doi2art.get(r["article_doi_upper"].strip().upper())
        if not (sw and art):
            missing.append(r["article_doi"])
            continue
        L.append(line(sw, "P1343", art, *report_ref(r)))   # software -> article
        n += 1
    print(f"  P1343 (new articles, explicit QIDs): {n}")
    if missing:
        print(f"  !! unresolved for {len(missing)} rows: {missing[:5]}")
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["software", "articles", "p1343"])
    ap.add_argument("out")
    ap.add_argument("--sw-report")
    ap.add_argument("--art-report")
    args = ap.parse_args()
    rows = load_prep()

    if args.phase == "software":
        L = phase_software(rows)
    elif args.phase == "articles":
        if not args.sw_report:
            sys.exit("articles phase needs --sw-report")
        L = phase_articles(rows, args.sw_report)
    else:
        if not (args.sw_report and args.art_report):
            sys.exit("p1343 phase needs --sw-report and --art-report")
        L = phase_p1343(rows, args.sw_report, args.art_report)

    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print(f"wrote {os.path.relpath(args.out, HERE)}   ({len(L)} QS lines)")


if __name__ == "__main__":
    main()
