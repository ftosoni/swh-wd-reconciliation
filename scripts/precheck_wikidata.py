#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""
precheck_wikidata.py -- READ-ONLY Wikidata lookup to flag duplicates before import.

For every venue import CSV (import/<venue>.csv, records mode) this script checks,
against the live Wikidata graph, whether the software node (by source-code
repository P1324 or SWHID P6138) and the article node (by DOI P356) already
exist. It writes an annotated copy with the resolved QIDs and a status flag, so
that OpenRefine enriches the existing item instead of minting a duplicate.

*** This script performs NO writes to Wikidata. It only issues SPARQL SELECTs. ***

Endpoints (2025 WDQS graph split):
  * software items (Q7397, P1324/P6138) live on the MAIN endpoint.
  * scholarly articles (P356) live on the SCHOLARLY subgraph endpoint.

Output:
  import/checked/<venue>.csv  -- original columns + existing_software_qid,
                                 software_status, existing_article_qid,
                                 article_status (filled on each record's first row)
  import/checked/_precheck_summary.csv
Raw SPARQL responses are cached under cache/wikidata_precheck/.
"""
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(HERE, "import")
OUT_DIR = os.path.join(IN_DIR, "checked")
CACHE_DIR = os.path.join(HERE, "cache", "wikidata_precheck")

MAIN_ENDPOINT = "https://query.wikidata.org/sparql"
SCHOLARLY_ENDPOINT = "https://query-scholarly.wikidata.org/sparql"
USER_AGENT = ("SWH-Wikidata-thesis-precheck/1.0 (read-only duplicate check; "
              "contact: Francesco.Tosoni@santannapisa.it)")

VENUES = ["SoftwareX", "JOSS", "JORS", "IPOL", "SIGMOD"]
DOI_BATCH = 150


# --------------------------------------------------------------------------- #
# SPARQL helper (GET with simple retry + on-disk cache)
# --------------------------------------------------------------------------- #
def sparql(endpoint, query, cache_key):
    import hashlib
    os.makedirs(CACHE_DIR, exist_ok=True)
    qhash = hashlib.md5(query.encode("utf-8")).hexdigest()[:8]
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}_{qhash}.json")
    if os.path.exists(cache_file):
        with open(cache_file, encoding="utf-8") as f:
            return json.load(f)
    body = urllib.parse.urlencode({"query": query, "format": "json"}).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=body,  # POST avoids URL-length limits on large VALUES lists
        headers={"User-Agent": USER_AGENT,
                 "Accept": "application/sparql-results+json",
                 "Content-Type": "application/x-www-form-urlencoded"})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read().decode("utf-8"))
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return data
        except Exception as e:  # noqa: BLE001
            last = e
            wait = 5 * (attempt + 1)
            print(f"    SPARQL retry {attempt + 1}/4 after error: {e} (sleep {wait}s)",
                  file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"SPARQL query failed for {cache_key}: {last}")


def bindings(data):
    return data.get("results", {}).get("bindings", [])


def qid(uri):
    return uri.rsplit("/", 1)[-1]


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def norm_repo(u):
    """Canonicalise a repository URL for cross-source equality."""
    if not u:
        return ""
    u = u.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.rstrip("/")
    u = re.sub(r"\.git$", "", u)
    return u


SWH_CORE = re.compile(r"swh:1:(?:cnt|dir|rev|rel|snp):[0-9a-f]{40}")


def swhid_cores(s):
    """Every intrinsic swh:1:<type>:<hash> token inside a (possibly qualified) SWHID."""
    return set(SWH_CORE.findall((s or "").lower()))


def norm_doi(d):
    return (d or "").strip().lower()


# --------------------------------------------------------------------------- #
# Wikidata lookups
# --------------------------------------------------------------------------- #
def fetch_repo_index():
    """Map normalised repo URL -> QID for every Wikidata item with P1324."""
    q = "SELECT ?item ?repo WHERE { ?item wdt:P1324 ?repo }"
    data = sparql(MAIN_ENDPOINT, q, "p1324_all")
    idx = {}
    for b in bindings(data):
        idx.setdefault(norm_repo(b["repo"]["value"]), qid(b["item"]["value"]))
    print(f"  Wikidata P1324 items indexed: {len(idx)}")
    return idx


def fetch_swhid_index():
    """Map intrinsic SWHID core -> QID for every Wikidata item with P6138."""
    q = "SELECT ?item ?swhid WHERE { ?item wdt:P6138 ?swhid }"
    data = sparql(MAIN_ENDPOINT, q, "p6138_all")
    idx = {}
    for b in bindings(data):
        for core in swhid_cores(b["swhid"]["value"]):
            idx.setdefault(core, qid(b["item"]["value"]))
    print(f"  Wikidata P6138 cores indexed: {len(idx)}")
    return idx


def fetch_doi_index(dois):
    """Map lower-cased DOI -> QID for the given DOIs, via the scholarly subgraph."""
    idx = {}
    dois = sorted({d for d in dois if d})
    for endpoint, tag in ((SCHOLARLY_ENDPOINT, "scholarly"), (MAIN_ENDPOINT, "main")):
        missing = [d for d in dois if d not in idx]
        if not missing:
            break
        ok = True
        for i in range(0, len(missing), DOI_BATCH):
            batch = missing[i:i + DOI_BATCH]
            # Wikidata stores DOIs upper-cased; query both cases to be safe.
            variants = []
            for d in batch:
                for v in (d, d.upper()):
                    variants.append(v.replace("\\", "\\\\").replace('"', '\\"'))
            values = " ".join('"%s"' % v for v in dict.fromkeys(variants))
            q = ("SELECT ?item ?doi WHERE { VALUES ?doi { %s } "
                 "?item wdt:P356 ?doi }" % values)
            try:
                data = sparql(endpoint, q, f"p356_{tag}_{i//DOI_BATCH:04d}")
            except Exception as e:  # noqa: BLE001
                print(f"    DOI lookup on {tag} endpoint failed ({e}); "
                      f"trying next endpoint.", file=sys.stderr)
                ok = False
                break
            for b in bindings(data):
                idx[norm_doi(b["doi"]["value"])] = qid(b["item"]["value"])
        if ok:
            break
    print(f"  Wikidata DOIs matched: {len(idx)} of {len(dois)}")
    return idx


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
# sw_item / art_item are the OpenRefine "subject" columns: the existing QID when
# the node already lives on Wikidata, else the literal "NEW". Having no blank
# cells lets OpenRefine mark every new row via "Use values as identifiers" +
# "Create a new item for each cell" without the facet-by-blank workaround.
NEW_MARKER = "NEW"
EXTRA_COLS = ["existing_software_qid", "software_status", "sw_item",
              "existing_article_qid", "article_status", "art_item"]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # 1) collect all DOIs up front (single scholarly pass), plus per-venue rows
    all_dois = set()
    venue_rows = {}
    for venue in VENUES:
        path = os.path.join(IN_DIR, f"{venue}.csv")
        if not os.path.exists(path):
            print(f"  SKIP {venue} (no CSV)")
            continue
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        venue_rows[venue] = rows
        for r in rows:
            if (r.get("article_doi") or "").strip():
                all_dois.add(norm_doi(r["article_doi"]))

    # 2) build Wikidata indices (read-only)
    print("Querying Wikidata (read-only)...")
    repo_idx = fetch_repo_index()
    swhid_idx = fetch_swhid_index()
    doi_idx = fetch_doi_index(all_dois)

    # 3) annotate and write
    summary = []
    for venue, rows in venue_rows.items():
        fieldnames = list(rows[0].keys()) + [c for c in EXTRA_COLS if c not in rows[0]]
        sw_exist = art_exist = papers = 0
        for r in rows:
            if not (r.get("article_doi") or "").strip():
                continue  # continuation (author) row
            papers += 1
            # software node: repo first, then SWHID
            sw_qid = repo_idx.get(norm_repo(r.get("repo_url_P1324")))
            if not sw_qid:
                for core in swhid_cores(r.get("swhid_P6138")):
                    if core in swhid_idx:
                        sw_qid = swhid_idx[core]
                        break
            r["existing_software_qid"] = sw_qid or ""
            r["software_status"] = "existing" if sw_qid else "new"
            r["sw_item"] = sw_qid or NEW_MARKER
            sw_exist += 1 if sw_qid else 0
            # article node
            art_qid = doi_idx.get(norm_doi(r.get("article_doi")))
            r["existing_article_qid"] = art_qid or ""
            r["article_status"] = "existing" if art_qid else "new"
            r["art_item"] = art_qid or NEW_MARKER
            art_exist += 1 if art_qid else 0

        out = os.path.join(OUT_DIR, f"{venue}.csv")
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        summary.append([venue, papers, sw_exist, papers - sw_exist,
                        art_exist, papers - art_exist])
        print(f"  {venue:10} papers={papers:5} "
              f"software[existing={sw_exist:4} new={papers-sw_exist:5}] "
              f"article[existing={art_exist:5} new={papers-art_exist:5}] "
              f"-> import/checked/{venue}.csv")

    with open(os.path.join(OUT_DIR, "_precheck_summary.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["venue", "papers", "software_existing", "software_new",
                    "article_existing", "article_new"])
        w.writerows(summary)
        if summary:
            w.writerow(["TOTAL", *[sum(c) for c in zip(*[s[1:] for s in summary])]])


if __name__ == "__main__":
    main()
