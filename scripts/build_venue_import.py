#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""
build_venue_import.py -- Flatten the enriched venue datasets into OpenRefine-ready
CSVs for node-level Wikidata import.

Scope: only the editorially-verified software journals / venues
(JOSS, SoftwareX, JORS, IPOL, SIGMOD ARI). The noisy mention-corpus
reconciliation track (reconcile_software.csv) is intentionally excluded.

Model (two distinct nodes per paper, matching the two-ontology design):
  * Article node  (scholarly article, Q13442814): DOI, title, venue, date,
    P921 main subject -> software node.
  * Software node (software, Q7397): P1324 source code repository, P6138 SWHID
    (qualified full form), P1343 described by source -> article node.

Reference blocks record the real provenance, one source per statement group:
  * Article statements   -> Crossref (crossref_ref_url / crossref_stated_in).
  * Software P1324 repo  -> the ElsevierSoftwareX mirror repository whose GitHub
    description carries the paper PII, the join key against Crossref
    (repo_ref_url_P854 / repo_stated_in). For SoftwareX this precise per-paper
    mirror URL is taken from softwarex_pairs.csv.
  * Software P6138 SWHID -> Software Heritage (set as a fixed value in the
    OpenRefine schema; the SWHID itself is its own archive locator).
Each reference is built in OpenRefine from P248 stated in, P854 reference URL,
and P813 retrieved.

Authors are kept OUT of the main table and emitted separately, one row per
author, so they can be imported in a dedicated later pass (typically restricted
to newly created articles, since pre-existing WikiCite articles already carry
their authors). This keeps the main table one-row-per-paper with a simple schema
and imposes no cap on the number of authors.

Outputs:
  import/<venue>.csv           -- one row per paper (article + software + refs)
  import/authors/<venue>.csv   -- one row per (paper, author): article_doi,
                                  series_ordinal (P1545), author_name (P2093)
  import/_summary.csv
"""
import csv
import html
import json
import os
import re
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "import")
AUTHORS_DIR = os.path.join(OUT_DIR, "authors")

# enriched file -> (venue tag, default language for P407 where Crossref has none)
VENUES = {
    "enriched_joss.json": ("JOSS", "en"),
    "enriched_softwarex.json": ("SoftwareX", "en"),
    "enriched_jors.json": ("JORS", "en"),
    "enriched_ipol.json": ("IPOL", "en"),
    "enriched_sigmod.json": ("SIGMOD", "en"),
}

SWH_ARCHIVE_PREFIX = "https://archive.softwareheritage.org/"
CODE_HOST = ("github.com", "gitlab.", "bitbucket.org", "sourceforge.net",
             "codeberg.org", "gitee.com")

COLUMNS = [
    "article_doi", "venue", "article_title",
    "software_label_from_repo", "software_label_from_title",
    "repo_url_P1324", "swhid_P6138",
    "publication_date_P577", "publisher_P123", "language_P407",
    "venue_name_P1433", "issn",
    "article_doi_url",
    # Article reference (Crossref): the bibliographic metadata source.
    "crossref_ref_url", "crossref_stated_in",
    # Software P1324 reference: where the paper<->repository link was found.
    "repo_ref_url_P854", "repo_stated_in",
    "retrieved_P813",
]
AUTHOR_COLUMNS = ["article_doi", "venue", "series_ordinal_P1545", "author_name_P2093"]


def clean_title(title):
    """Normalise a Crossref title for use as a Wikidata label / P1476 value.

    Crossref returns JATS/MathML markup (e.g. <mml:math ...>, <i>, <sub>) and
    pretty-printing whitespace (embedded newlines + indentation) inside titles.
    A Wikidata label/title must be a single clean line, so we strip tags, decode
    HTML entities, and collapse all whitespace runs to a single space."""
    if not title:
        return ""
    t = html.unescape(title)                    # decode entities first: &lt;i&gt; -> <i>
    t = re.sub(r"</?[A-Za-z][^<>]*>", " ", t)   # drop tags (letter-anchored: keeps "a < b")
    t = re.sub(r"\s+", " ", t).strip()          # collapse newlines/tabs/repeats
    return t


def extract_swhid(rec):
    """Return the qualified-full SWHID for a record, or '' if none.

    JOSS/SoftwareX/JORS/SIGMOD already carry it in `swhid`.
    IPOL nests it inside `repo_url` as an archive.softwareheritage.org URL.
    """
    s = (rec.get("swhid") or "").strip()
    if s.startswith("swh:1:"):
        return s
    repo = (rec.get("repo_url") or "").strip()
    if repo.startswith(SWH_ARCHIVE_PREFIX):
        cand = repo[len(SWH_ARCHIVE_PREFIX):]
        if cand.startswith("swh:1:"):
            return cand
    return ""


def repo_for_p1324(rec):
    """The P1324 value: keep only genuine code-host repository URLs.

    Drops archive.softwareheritage.org URLs (that content lives in P6138) and
    non-repository landing pages / tarballs (e.g. IPOL src.tar.gz)."""
    repo = (rec.get("repo_url") or "").strip()
    if not repo or repo.startswith(SWH_ARCHIVE_PREFIX):
        return ""
    host = (urlsplit(repo).netloc or "").lower()
    if any(h in host for h in CODE_HOST):
        return repo
    return ""


def label_from_repo(repo):
    if not repo:
        return ""
    path = urlsplit(repo).path.strip("/")
    if not path:
        return ""
    seg = path.split("/")[-1]
    return re.sub(r"\.git$", "", seg)


def label_from_title(title):
    if not title:
        return ""
    for sep in (":", " - ", " – ", " — "):
        if sep in title:
            head = title.split(sep, 1)[0].strip()
            if 0 < len(head) <= 40:
                return head
    return ""


def load_softwarex_mirrors():
    """Map DOI -> ElsevierSoftwareX mirror repo URL, the precise per-paper
    evidence for the repo mapping (its GitHub description carries the PII join
    key). Falls back to {} if softwarex_pairs.csv is absent."""
    path = os.path.join(HERE, "softwarex_pairs.csv")
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            doi = (row.get("doi") or "").strip()
            mirror = (row.get("elsevier_mirror_url") or "").strip()
            if doi and mirror:
                out[doi] = mirror
    return out


def language(rec, default):
    cr = (rec.get("metadata") or {}).get("crossref_raw") or {}
    return cr.get("language") or default


def author_names(rec):
    """Ordered author display names, preserving Crossref order."""
    out = []
    for a in (rec.get("metadata") or {}).get("authors") or []:
        gn = (a.get("given_name") or "").strip()
        fn = (a.get("family_name") or "").strip()
        name = " ".join(p for p in (gn, fn) if p)
        if name:
            out.append(name)
    return out


def build_row(rec, venue, default_lang, mirror_map):
    m = rec.get("metadata") or {}
    prov = rec.get("provenance") or {}
    rm = prov.get("repository_mapping") or {}
    bib = prov.get("bibliographic_metadata") or {}
    repo = repo_for_p1324(rec)
    doi = rec.get("resolved_doi") or rec.get("publication_id") or ""
    title = clean_title(m.get("title") or "")
    issns = m.get("issns") or []
    # Reference URL for the P1324 repository statement. Prefer the precise
    # per-paper SoftwareX mirror (PII-bearing) over the org-level listing.
    repo_ref_url = (rm.get("reference_url") or "").strip()
    if venue == "SoftwareX" and doi in mirror_map:
        repo_ref_url = mirror_map[doi]
    return {
        "article_doi": doi,
        "venue": venue,
        "article_title": title,
        "software_label_from_repo": label_from_repo(repo),
        "software_label_from_title": label_from_title(title),
        "repo_url_P1324": repo,
        "swhid_P6138": extract_swhid(rec),
        "publication_date_P577": m.get("publication_date") or "",
        "publisher_P123": m.get("publisher") or "",
        "language_P407": language(rec, default_lang),
        "venue_name_P1433": m.get("venue") or "",
        "issn": issns[0] if issns else "",
        "article_doi_url": f"https://doi.org/{doi}" if doi else "",
        "crossref_ref_url": (bib.get("reference_url") or "").strip(),
        "crossref_stated_in": bib.get("stated_in") or "",
        "repo_ref_url_P854": repo_ref_url,
        "repo_stated_in": rm.get("stated_in") or "",
        "retrieved_P813": rm.get("retrieved") or bib.get("retrieved") or "",
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(AUTHORS_DIR, exist_ok=True)
    mirror_map = load_softwarex_mirrors()
    summary = []
    for fname, (venue, default_lang) in VENUES.items():
        path = os.path.join(HERE, fname)
        if not os.path.exists(path):
            print(f"  SKIP {fname} (not found)")
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        rows, author_rows, n_repo, n_swh, n_url = [], [], 0, 0, 0
        for rec in data:
            row = build_row(rec, venue, default_lang, mirror_map)
            n_repo += 1 if row["repo_url_P1324"] else 0
            n_swh += 1 if row["swhid_P6138"] else 0
            n_url += 1 if row["repo_ref_url_P854"] else 0
            rows.append(row)
            for idx, name in enumerate(author_names(rec), start=1):
                author_rows.append({
                    "article_doi": row["article_doi"],
                    "venue": venue,
                    "series_ordinal_P1545": idx,
                    "author_name_P2093": name,
                })

        out = os.path.join(OUT_DIR, f"{venue}.csv")
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)

        aout = os.path.join(AUTHORS_DIR, f"{venue}.csv")
        with open(aout, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=AUTHOR_COLUMNS)
            w.writeheader()
            w.writerows(author_rows)

        summary.append([venue, len(rows), n_repo, n_swh, n_url, len(author_rows)])
        print(f"  {venue:10} papers={len(rows):5} P1324={n_repo:5} P6138={n_swh:5} "
              f"refURL={n_url:5} authorRows={len(author_rows):6} -> import/{venue}.csv (+authors/)")

    with open(os.path.join(OUT_DIR, "_summary.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["venue", "papers", "with_P1324_repo", "with_P6138_swhid",
                    "with_ref_url", "author_rows"])
        w.writerows(summary)
        if summary:
            w.writerow(["TOTAL", *[sum(c) for c in zip(*[s[1:] for s in summary])]])
    print(f"  {'TOTAL':10} papers={sum(s[1] for s in summary)} "
          f"authorRows={sum(s[5] for s in summary)}")


if __name__ == "__main__":
    main()
