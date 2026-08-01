#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""
prep_openrefine.py -- Deterministic pre-processing of a venue's checked import CSV
into an OpenRefine-ready CSV, so that NO cleanup happens inside OpenRefine.

Reads  import/checked/<venue>.csv   (output of precheck_wikidata.py)
Writes import/checked/<venue>.prep.csv

All transforms are reproducible here rather than as OpenRefine GREL, keeping the
upstream pipeline files untouched. A full per-change report is printed to stdout.

Transforms
----------
1. repo_url_P1324 -- ROOT normalisation. A URL that points at a branch view, a
   paper subfolder, or a hosting web-view page (wiki/issues/releases/tag/...)
   rather than the project itself is trimmed to its repository root by cutting at
   the first branch/web-view marker segment (/tree/ /blob/ /src/ /-/ /ci/ /commit/
   /raw/ /wiki/ /issues/ /pull/ /releases/ /tags/ /milestones/ /actions/ ..., each
   also matched at end-of-URL). Trailing slashes stripped. For every row whose URL
   changed, software_label_from_repo is re-derived as the last path segment of the
   cleaned root (repairs junk labels such as 'master'/'joss'/'wiki'/'releases').

2. Wrong-repo DROP -- rows whose repo's last path segment is a paper-repo marker
   ('paper', 'joss_paper', 'joss-paper') are dropped: the harvester captured the
   paper's own support repository, not the software (mirror of the SoftwareX
   PhotonSTR drop). The dropped rows are listed in the report.

3. Generic-label RELABEL -- when the repo's last segment is a generic module name
   ('core', 'framework'), the label is re-derived from the PARENT path segment
   (e.g. .../remix/framework -> 'remix', BioGearsEngine/core -> 'BioGearsEngine'),
   which is the real project identity and also removes the label collision.

4. Case-variant repo CANONICALISATION -- repositories written with different
   letter-casing (e.g. StingraySoftware/stingray vs stingraysoftware/stingray) are
   the same GitHub repo; all raw spellings in a canonical-collision group are
   unified to a single form (the one with the most upper-case letters, i.e. the
   project's own casing) so they reconcile to ONE software item and emit ONE
   distinct P1324 value.

5. software_desc -- description column for the software item. Base text
   "software described in <VENUE>"; for a label shared by two or more DISTINCT
   repositories among the new rows, " (owner/repo)" is appended so that
   label+description stays unique (Wikidata rejects two items with identical
   label+description in a language). This is the fix for the hard "identical
   labels and descriptions" upload blocker.

6. SWHID dedup on shared repos -- when several rows share one repository (one
   software item, cited by several papers), swhid_P6138 is kept on only the first
   such row and blanked on the rest, so the merged item carries a single P6138
   (avoids the "SWHID added more than once" single-value violation).

7. web_interface_qid -- host -> Wikidata item for the P1324 qualifier P10627.
8. article_doi_upper -- DOI upper-cased for P356 (Wikidata convention + dedup).
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKED_DIR = os.path.join(HERE, "import", "checked")

MARKER = re.compile(
    r"/(?:tree|blob|src|-|ci|commit|commits|raw|wiki|issues|issue|pull|pulls|"
    r"releases|tags|milestones|actions|network|graphs|discussions)(?:/|$)",
    re.IGNORECASE)
DROP_LASTSEG = {"paper", "joss_paper", "joss-paper"}      # paper-repo, not software
GENERIC_LASTSEG = {"core", "framework"}                    # relabel to parent segment

# Venues with NO source repositories, where the archival anchor is the native
# SWHID alone (IPOL deposits code into Software Heritage by design). A row with
# neither a repo nor a SWHID has no anchor at all and is dropped; the software
# label is derived from the article title (leading name segment before ':'/' - ',
# else the whole title), since these implementations have no independent name.
SWHID_ONLY_VENUES = {"IPOL"}
_TITLE_SPLIT = re.compile(r"\s*(?::| - )")

# Venues where the harvested repo is often a *reproducibility artifact* whose slug is
# junk (e.g. `grafite-experiments`, `pimpam-reproduce`), while the harvester's
# title-derived name IS the real tool name when it is a single clean token (Grafite,
# PimPam). For these venues, prefer the title token as the label when it is a single
# clean token; otherwise fall back to the repo slug. The title-derived value is never
# emitted as an alias (a multi-word title is the paper title, not a software alias).
TITLE_TOKEN_LABEL_VENUES = {"SIGMOD"}
_CLEAN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_+.-]*")

# Per-DOI canonical software labels: the repo is a reproducibility artifact whose
# tool name is NOT a clean title token (the title is the paper title), but the tool
# name is recoverable (repo stem or article title). Set explicitly; alias suppressed.
LABEL_FIX = {
    "SIGMOD": {
        "10.1145/3626768": "SQLSolver",            # repo sjtu-ipads/sqlsolver-artifacts
        "10.1145/3448016.3457284": "DivExplorer",  # repo elianap/divexplorer_sigmod21_experiments
        "10.1145/3448016.3452791": "AU-DB",        # repo fengsu91/audb_reproducibility
        "10.1145/3448016.3457320": "Clonos",       # article title is "Clonos"
        "10.1145/3448016.3457244": "Tuplex",        # repo leonhardfs/tuplex-public, title "Tuplex"
    },
}

# Per-row article class (P31) and published-in (P1433), keyed on the venue string.
# SIGMOD spans two publication types: PACMMOD is a scientific journal (its papers are
# academic journal articles); the 2020/2021/2022 "International Conference on
# Management of Data" volumes are genuine conference proceedings (conference papers).
# Only PACMMOD exists as a Wikidata item (Q130602410, confirmed from the 22 existing
# WikiCite articles); the three conference volumes have NO item, so P1433 is omitted
# for those rows (WikiCite itself leaves them without a published-in).
ARTICLE_CLASS_CONFERENCE = "Q23927052"   # conference paper (default for SIGMOD)
VENUE_CLASS_P31 = {
    "SIGMOD": {
        "Proceedings of the ACM on Management of Data": "Q18918145",  # journal article
    },
}
VENUE_QID_P1433 = {
    "SIGMOD": {
        "Proceedings of the ACM on Management of Data": "Q130602410",
    },
}


def title_label(article_title):
    """Leading name segment of an IPOL article title (before ':' or ' - '),
    falling back to the full title. Reproduces the harvester's
    software_label_from_title exactly and extends it to untitled-software rows."""
    t = (article_title or "").strip()
    return _TITLE_SPLIT.split(t, maxsplit=1)[0].strip() if t else ""

# --- venue-specific per-DOI editorial corrections (see README §JORS) ---
# URL_FIX: the harvested URL was a raw-file / GitHub-Pages / whitespace-mangled
# variant of a REAL repository; rewrite it to the repo root (label re-derived).
URL_FIX = {
    "JORS": {
        "10.5334/jors.af": "https://github.com/smarciuska/feature-usage-explorer",
        "10.5334/jors.aw": "https://github.com/lattice/quda",
        "10.5334/jors.aj": "https://github.com/forstermatth/LIIS",
    },
}
# ROW_DROP: rows whose harvested URL is NOT the paper's own source-code repo and
# which carry no SWHID either -> no SWH<->Wikidata anchor, so not imported.
#   B = generic doc/marketing/meeting page (docs.github.com, git-lfs, about.gitlab,
#       a WSSSPE/meetings issue thread);
#   C = real/plausible software but only a project homepage or no repo at all,
#       and no SWHID (incl. 4 name-from-title-only rows with neither repo nor SWHID).
ROW_DROP = {
    "JORS": {
        "10.5334/jors.307", "10.5334/jors.548", "10.5334/jors.245", "10.5334/jors.118",
        "10.5334/jors.ak", "10.5334/jors.73", "10.5334/jors.123", "10.5334/jors.185",
        "10.5334/jors.125", "10.5334/jors.289", "10.5334/jors.bb", "10.5334/jors.193",
        "10.5334/jors.bi", "10.5334/jors.103", "10.5334/jors.89", "10.5334/jors.ai",
        "10.5334/jors.ae",
        # bare-host `https://github.com` (0 path segments, label "github.com"), all
        # wrongly deduped to the same existing junk item Q138410521; no SWHID. Two
        # are workshop reports. (Missed in the first pass: netloc github.com is a
        # mapped host, so the earlier weird-host scan did not flag them.)
        "10.5334/jors.an", "10.5334/jors.114", "10.5334/jors.242", "10.5334/jors.334",
        # dependency-repo mismatch: the harvested repo is a DEPENDENCY, not the
        # paper's own software, and it deduped onto an existing item for that
        # dependency. jors.680 (SAMannot, based on SAM2) -> ultralytics/ultralytics
        # (Q131738293); jors.bj (PyRDM) -> requests/requests-oauthlib (Q107380975).
        # Dropping avoids attaching the paper's alias/desc/cross-links to the wrong
        # (dependency) item. Found after the existing-SWHID upload, which had to be
        # reverted on those two items.
        "10.5334/jors.680", "10.5334/jors.bj",
    },
    "SIGMOD": {
        # shared reproducibility mono-repo: one `damslab/reproducibility` repo cited
        # by 5 different Apache SystemDS tools (GIO, AWARE, LIMA, ExDRa, SliceLine).
        # A single repo cannot be the own source repository of 5 distinct software
        # items; dropped for safety (the harvested repo is the lab's reproduction
        # scripts, not each tool's source).
        "10.1145/3589265", "10.1145/3588682", "10.1145/3448016.3452788",
        "10.1145/3448016.3457549", "10.1145/3448016.3457323",
        # no repo, no SWHID, no derivable label.
        "10.1145/3318464.3380598",
        # reproducibility-artifact repos (name is a generic `sigmodNN`/`reproducibility`
        # slug, NOT the tool's own source repository) AND the paper names no tool from
        # which to derive a clean label -> would mint low-quality, mislabelled items.
        # Same category as the damslab mono-repo but one-paper-per-repo. Dropped after
        # the audit-before-upload review (see README §11, manuscript footnote). Four
        # sibling artifact repos with a *recoverable* tool name are kept and relabelled
        # via LABEL_FIX (SQLSolver, DivExplorer, AU-DB, Clonos).
        "10.1145/3617331", "10.1145/3588946", "10.1145/3448016.3452795",
        "10.1145/3448016.3452798", "10.1145/3448016.3457278",
        "10.1145/3318464.3389697", "10.1145/3318464.3389732",
    },
}

# SWHID_FIX: per-DOI replacement of a malformed swhid_P6138. The IPOL page scrape
# concatenated MULTIPLE archived SWHIDs with spaces into one cell (multiple visits
# of the same origin), producing a value that fails the Wikidata P6138 format
# regex. Each replacement is the single authoritative qualified-full SWHID, taken
# from the Software Heritage API (origin -> latest visit -> snapshot HEAD revision
# -> directory) and verified to resolve (HTTP 200). See README §11.
SWHID_FIX = {
    "IPOL": {
        "10.5201/ipol.2014.68":
            "swh:1:dir:303f6f27e19266ee3baa7b8b008fab322be305d6;"
            "origin=https://doi.org/10.5201/ipol.2014.68;"
            "visit=swh:1:snp:7f248e8b1d607b5f817a29a1034fc0e39ee458d5;"
            "anchor=swh:1:rev:84400ed5fe147164bac503dbf7b21cf133325ddc",
        "10.5201/ipol.2011.llmps-scb":
            "swh:1:dir:b2256bdc9c77015bfe72feeed56d6e34281a2cc8;"
            "origin=https://doi.org/10.5201/ipol.2011.llmps-scb;"
            "visit=swh:1:snp:990466e5cd9992958e3ff727eafb8fa42b45fdcc;"
            "anchor=swh:1:rev:b3b24e613f61864de6dce9dded8469e124490441",
    },
}

# ROW_SPLIT: a single harvested row that actually describes TWO (or more) distinct
# software items sharing ONE article (mirror of the SoftwareX one-paper-two-repo
# case). Each entry replaces the row with one row per implementation, carrying its
# own SWHID and a description tag; the article columns are left identical so the
# rows reconcile to a single shared article item. IPOL cm_fds ("Finite Difference
# Schemes for MCM and AMSS") bundles the AMSS and MCM implementations, each archived
# under its own SWH sub-origin (cm_fds_amss / cm_fds_mcm). Both SWHIDs verified to
# resolve (HTTP 200). See README §11 and the manuscript footnote.
ROW_SPLIT = {
    "IPOL": {
        "10.5201/ipol.2011.cm_fds": [
            {"tag": "AMSS", "swhid":
                "swh:1:dir:c1bbe6e94d7637bad503f300a0ce3e77d3799bc2;"
                "origin=https://doi.org/10.5201/ipol.2011.cm_fds_amss;"
                "visit=swh:1:snp:f0a7ac2efed4a724795bbf05f1dd9722b7b53c70;"
                "anchor=swh:1:rev:82c9018741d6fc7e868d14d433963ca0a5184f35"},
            {"tag": "MCM", "swhid":
                "swh:1:dir:f062e98d424614f268112efe71a32b75f09f9c83;"
                "origin=https://doi.org/10.5201/ipol.2011.cm_fds_mcm;"
                "visit=swh:1:snp:270c11cf1935c734fa13bfe77b83353c7e7860e9;"
                "anchor=swh:1:rev:f13c6bcc8d1ad987925d0bdca6fde01e7e95fce2"},
        ],
    },
}

WEB_INTERFACE_QID = {
    "github.com": "Q364", "www.github.com": "Q364",
    "gitlab.com": "Q16639197", "bitbucket.org": "Q2493781",
    "codeberg.org": "Q106102182", "sourceforge.net": "Q165400",
}


def web_interface_qid(host):
    host = host.lower()
    if host in WEB_INTERFACE_QID:
        return WEB_INTERFACE_QID[host]
    if "gitlab" in host:
        return "Q16639197"
    return ""


def clean_repo_url(url):
    m = MARKER.search(url)
    return (url[:m.start()] if m else url).rstrip("/")


def path_segments(url):
    return [s for s in urlparse(url).path.split("/") if s]


def canon_key(url):
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = re.sub(r"\.git$", "", u)
    return u.rstrip("/")


def owner_repo(url):
    segs = path_segments(url)
    return "/".join(segs[-2:]) if len(segs) >= 2 else (segs[-1] if segs else url)


def label_of(r):
    return r["software_label_from_repo"] or r["software_label_from_title"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("venue")
    ap.add_argument("--venue-label", default="Journal of Open Source Software",
                    help="venue name used inside the description strings")
    ap.add_argument("--in", dest="inp")
    ap.add_argument("--out", dest="out")
    args = ap.parse_args()

    inp = args.inp or os.path.join(CHECKED_DIR, f"{args.venue}.csv")
    out = args.out or os.path.join(CHECKED_DIR, f"{args.venue}.prep.csv")
    if not os.path.exists(inp):
        sys.exit(f"ERROR: input not found: {inp}")
    VENUE = args.venue_label
    BASE_DESC = f"software described in {VENUE}"
    ARTICLE_BASE_DESC = f"scientific article published in {VENUE}"

    with open(inp, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        cols = list(reader.fieldnames)
    for c in ("web_interface_qid", "article_doi_upper", "software_desc",
              "software_label", "software_alias", "article_desc",
              "article_class_P31", "venue_qid_P1433"):
        if c not in cols:
            cols.append(c)

    rep = {"url": [], "label": [], "dropped": [], "canon": [], "swhid": [], "desc": [],
           "editdrop": [], "editfix": [], "noanchor": [], "swhidfix": [], "split": []}

    # ---- 0. venue-specific editorial corrections (per-DOI URL fix / row drop) ----
    url_fix = URL_FIX.get(args.venue, {})
    drop_dois = ROW_DROP.get(args.venue, set())
    swhid_fix = SWHID_FIX.get(args.venue, {})
    row_split = ROW_SPLIT.get(args.venue, {})
    swhid_only = args.venue in SWHID_ONLY_VENUES
    split_dois = set()
    kept = []
    for r in rows:
        doi = r["article_doi"].strip()
        if doi in drop_dois:
            rep["editdrop"].append((label_of(r), r.get("repo_url_P1324", ""), doi))
            continue
        if doi in swhid_fix:
            rep["swhidfix"].append((doi, r.get("swhid_P6138", ""), swhid_fix[doi]))
            r["swhid_P6138"] = swhid_fix[doi]
        if swhid_only and not (r.get("repo_url_P1324") or "").strip() \
                and not (r.get("swhid_P6138") or "").strip():
            # no repo AND no SWHID -> no archival anchor; drop (see SWHID_ONLY_VENUES)
            rep["noanchor"].append((r.get("article_title", ""), doi))
            continue
        if swhid_only and not (r.get("software_label_from_title") or "").strip():
            # repo-less venue with no harvester name: derive from the article title
            # up front, so software_desc collision-keying and the label/alias
            # hygiene below both see the real label.
            r["software_label_from_title"] = title_label(r.get("article_title", ""))
        if doi in url_fix:
            new = url_fix[doi]
            rep["editfix"].append((r.get("repo_url_P1324", ""), new, doi))
            r["repo_url_P1324"] = new
            r["software_label_from_repo"] = new.rstrip("/").split("/")[-1]
        if doi in row_split:
            # one article -> N software items; emit one row per implementation,
            # article columns untouched so the rows reconcile to ONE shared article.
            split_dois.add(doi)
            for spec in row_split[doi]:
                c = dict(r)
                c["swhid_P6138"] = spec["swhid"]
                c["_desc_tag"] = spec["tag"]  # honoured in step 5, stripped on write
                rep["split"].append((doi, spec["tag"]))
                kept.append(c)
            continue
        kept.append(r)
    rows = kept

    # ---- 1. URL root normalisation + label re-derive; 8. doi_upper ----
    for r in rows:
        r["article_doi_upper"] = r["article_doi"].strip().upper()
        url = (r.get("repo_url_P1324") or "").strip()
        if not url:
            continue
        cleaned = clean_repo_url(url)
        if cleaned != url:
            new_label = cleaned.split("/")[-1]
            rep["url"].append((url, cleaned))
            if new_label and new_label != r.get("software_label_from_repo", ""):
                rep["label"].append((r["software_label_from_repo"], new_label, cleaned))
                r["software_label_from_repo"] = new_label
            r["repo_url_P1324"] = cleaned

    # ---- 2. drop wrong-repo rows ----
    kept = []
    for r in rows:
        segs = path_segments(r.get("repo_url_P1324") or "")
        if segs and segs[-1].lower() in DROP_LASTSEG:
            rep["dropped"].append((label_of(r), r["repo_url_P1324"], r["article_doi"]))
        else:
            kept.append(r)
    rows = kept

    # ---- 3. relabel generic-module labels to the parent segment ----
    for r in rows:
        if r["software_status"] != "new":
            continue
        segs = path_segments(r.get("repo_url_P1324") or "")
        if len(segs) >= 2 and segs[-1].lower() in GENERIC_LASTSEG:
            parent = segs[-2]
            old = r["software_label_from_repo"]
            if parent and parent != old:
                r["software_label_from_repo"] = parent
                rep["label"].append((old, parent, r["repo_url_P1324"]))

    # ---- 4. canonicalise case-variant repositories (same repo, different casing) ----
    groups = defaultdict(set)
    for r in rows:
        if r.get("repo_url_P1324"):
            groups[canon_key(r["repo_url_P1324"])].add(r["repo_url_P1324"])
    canonical = {}
    for k, variants in groups.items():
        if len(variants) > 1:
            chosen = max(variants, key=lambda u: (sum(c.isupper() for c in u), u))
            canonical[k] = chosen
            rep["canon"].append((sorted(variants), chosen))
    for r in rows:
        u = r.get("repo_url_P1324")
        if u and canon_key(u) in canonical:
            new = canonical[canon_key(u)]
            if new != u:
                r["repo_url_P1324"] = new
                r["software_label_from_repo"] = new.split("/")[-1]

    # ---- 7. web_interface_qid (after all URL edits) ----
    for r in rows:
        u = r.get("repo_url_P1324") or ""
        r["web_interface_qid"] = web_interface_qid(urlparse(u).netloc) if u else ""

    # ---- 5. software_desc with per-collision disambiguation (new rows) ----
    # merge_key = the identity that OpenRefine reconciliation collapses to ONE item:
    # the repo URL, or (repo-less SWHID venues) the SWHID, else the DOI. Rows sharing
    # a key are the SAME software, so they must NOT be counted as a label collision
    # (they merge and must carry ONE description).
    def merge_key(r):
        if r.get("repo_url_P1324"):
            return r["repo_url_P1324"]
        if swhid_only and (r.get("swhid_P6138") or "").strip():
            return r["swhid_P6138"].strip()
        return r["article_doi"]
    lab2repos = defaultdict(set)
    for r in rows:
        # split rows share an article and a label on purpose; they are disambiguated
        # by their explicit _desc_tag, so keep them out of the auto-collision keying.
        if r["software_status"] == "new" and not r.get("_desc_tag"):
            lab2repos[label_of(r)].add(merge_key(r))
    colliding = {lab for lab, reps in lab2repos.items() if len(reps) > 1}
    for r in rows:
        if r.get("_desc_tag"):
            r["software_desc"] = f"{BASE_DESC} ({r['_desc_tag']})"
            rep["desc"].append((label_of(r), r["software_desc"]))
            continue
        if r["software_status"] != "new":
            r["software_desc"] = BASE_DESC
            continue
        if label_of(r) in colliding:
            disc = owner_repo(r["repo_url_P1324"]) if r.get("repo_url_P1324") else r["article_doi"]
            r["software_desc"] = f"{BASE_DESC} ({disc})"
            rep["desc"].append((label_of(r), r["software_desc"]))
        else:
            r["software_desc"] = BASE_DESC

    # ---- 6. SWHID dedup across rows sharing one repository ----
    seen_repo_with_swhid = set()
    byrepo = defaultdict(list)
    for r in rows:
        if r.get("repo_url_P1324"):
            byrepo[r["repo_url_P1324"]].append(r)
    for repo, group in byrepo.items():
        if len(group) < 2:
            continue
        kept_one = False
        for r in group:
            if r.get("swhid_P6138"):
                if not kept_one:
                    kept_one = True
                else:
                    rep["swhid"].append((repo, r["article_doi"]))
                    r["swhid_P6138"] = ""

    # ---- label + alias hygiene ----
    # software_label: repo-derived name, falling back to the title-derived one so
    # the repo-less rows are not created without a label.
    # software_alias: the title-derived name only when it actually differs from the
    # label, so we never emit a redundant alias equal to the label.
    # For TITLE_TOKEN_LABEL_VENUES (SIGMOD) the repo slug is usually a reproducibility
    # artifact name: prefer the single-clean-token title as the label, honour the
    # per-DOI LABEL_FIX overrides, and never emit an alias (the multi-word title is
    # the paper title, not a software alias).
    label_fix = LABEL_FIX.get(args.venue, {})
    title_token_venue = args.venue in TITLE_TOKEN_LABEL_VENUES
    for r in rows:
        repo = r["software_label_from_repo"].strip()
        title = r["software_label_from_title"].strip()
        if title_token_venue:
            doi = r["article_doi"].strip()
            if doi in label_fix:
                r["software_label"] = label_fix[doi]
            elif title and _CLEAN_TOKEN.fullmatch(title):
                r["software_label"] = title
                rep["label"].append((repo or title, title, r.get("repo_url_P1324", "")))
            else:
                r["software_label"] = repo or title
            r["software_alias"] = ""
        else:
            label = repo or title
            r["software_label"] = label
            r["software_alias"] = title if (title and title != label) else ""

    # ---- article_desc with per-collision disambiguation (new articles) ----
    # The article description is otherwise a fixed string; two DISTINCT new articles
    # (different DOI) that share a title would collide on label+description (Wikidata
    # rejects that). For such titles the DOI is appended so each stays unique. Rows
    # that share ONE article (same DOI, e.g. the cm_fds split) are not a collision.
    title2dois = defaultdict(set)
    for r in rows:
        if r["article_status"] == "new":
            title2dois[r["article_title"]].add(r["article_doi"])
    art_colliding = {t for t, d in title2dois.items() if len(d) > 1}
    for r in rows:
        if r["article_status"] == "new" and r["article_title"] in art_colliding:
            r["article_desc"] = f"{ARTICLE_BASE_DESC} ({r['article_doi']})"
        else:
            r["article_desc"] = ARTICLE_BASE_DESC

    # ---- per-row article class (P31) + published-in (P1433) ----
    # For venues that span more than one publication type (SIGMOD: PACMMOD journal vs
    # conference proceedings). Class defaults to conference paper; P1433 is the QID of
    # the specific proceedings when a Wikidata item exists (else blank -> statement
    # skipped by the schema/QS builder, matching WikiCite for the item-less volumes).
    class_map = VENUE_CLASS_P31.get(args.venue, {})
    p1433_map = VENUE_QID_P1433.get(args.venue, {})
    for r in rows:
        if args.venue in VENUE_CLASS_P31 or args.venue in VENUE_QID_P1433:
            vn = r["venue_name_P1433"]
            r["article_class_P31"] = class_map.get(vn, ARTICLE_CLASS_CONFERENCE)
            r["venue_qid_P1433"] = p1433_map.get(vn, "")
        else:
            r["article_class_P31"] = ""
            r["venue_qid_P1433"] = ""

    # ---- write ----
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")  # drops _desc_tag
        w.writeheader()
        w.writerows(rows)

    # ---- report ----
    print(f"{args.venue}: {len(rows)} rows -> {os.path.relpath(out, HERE)}")
    print(f"  0. editorial URL fixes: {len(rep['editfix'])}   rows dropped (editorial): {len(rep['editdrop'])}")
    if rep["swhidfix"]:
        print(f"  0a. malformed SWHIDs corrected: {len(rep['swhidfix'])}")
        for doi, old, new in rep["swhidfix"]:
            print(f"       {doi}: -> {new}")
    if rep["split"]:
        print(f"  0c. rows split (one article -> N software): {len(rep['split'])}")
        for doi, tag in rep["split"]:
            print(f"       {doi} -> ({tag})")
    if rep["noanchor"]:
        print(f"  0b. rows dropped (no repo AND no SWHID = no anchor): {len(rep['noanchor'])}")
    for old, new, doi in rep["editfix"]:
        print(f"       fix {doi}: {old} -> {new}")
    for lab, url, doi in rep["editdrop"]:
        print(f"       drop {doi}: {lab!r} {url}")
    print(f"  1. URLs root-cleaned: {len(rep['url'])}   labels re-derived/relabelled: {len(rep['label'])}")
    print(f"  2. wrong-repo rows DROPPED: {len(rep['dropped'])}")
    for lab, url, doi in rep["dropped"]:
        print(f"       {lab!r} {url}  ({doi})")
    print(f"  4. case-variant repos unified: {len(rep['canon'])}")
    for variants, chosen in rep["canon"]:
        print(f"       {variants} -> {chosen}")
    print(f"  5. descriptions disambiguated (colliding labels): {len(rep['desc'])}")
    for lab, desc in rep["desc"]:
        print(f"       {lab!r}: {desc}")
    print(f"  6. duplicate SWHIDs blanked on shared repos: {len(rep['swhid'])}")

    wi = Counter(r["web_interface_qid"] or "(none)" for r in rows)
    name = {"Q364": "GitHub", "Q16639197": "GitLab", "Q2493781": "Bitbucket",
            "Q106102182": "Codeberg", "Q165400": "SourceForge", "(none)": "(no repo)"}
    print("  7. web_interface_qid distribution:")
    for qid, n in wi.most_common():
        print(f"       {qid:12} {name.get(qid, ''):12} {n}")

    # ---- self-check: nothing should still collide ----
    # keyed on merge_key so a shared-item group (same repo/SWHID cited by >1 paper),
    # which collapses to ONE item in reconciliation, is not counted as a collision.
    lab2repos = defaultdict(set)
    for r in rows:
        if r["software_status"] == "new":
            lab2repos[(label_of(r), r["software_desc"])].add(merge_key(r))
    still = {k: v for k, v in lab2repos.items() if len(v) > 1}
    print(f"  CHECK label+desc collisions remaining (new sw): {len(still)}")
    for (lab, desc), reps in list(still.items())[:10]:
        print(f"       !! {lab!r} / {desc!r} -> {sorted(reps)}")


if __name__ == "__main__":
    main()
