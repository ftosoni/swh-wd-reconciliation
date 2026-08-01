#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""
harvest_venues.py  --  Download and harvest DOI -> repository associations
for scholarly software venues: JOSS, JORS, SoftwareX, and IPOL.

Uses official JSON APIs (JOSS, Crossref, GitHub) to obtain structured metadata
whenever possible to avoid aggressive HTML page scraping.
"""
import argparse
import csv
import hashlib
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from bs4 import BeautifulSoup

# Setup paths
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(HERE, "scrape_venues.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

# Load configuration if available
CONFIG = {}
config_path = os.path.join(HERE, "config.json")
if os.path.exists(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            CONFIG = json.load(f)
    except Exception as e:
        logging.warning(f"Failed to load config.json: {e}")

UA = CONFIG.get("user_agent", "SWH-Wikidata-thesis-scraper/1.0 (research; mailto:Francesco.Tosoni@santannapisa.it)")

CODE_HOST = re.compile(
    r"(github\.com|gitlab\.com|bitbucket\.org|sourceforge\.net|code\.google\.com|"
    r"savannah\.(non)?gnu\.org|gitorious|git\.|/git/|launchpad\.net|codeberg\.org)", re.I)


def get_cached_url(url, cache_type="html", sleep_sec=1.0, post_data=None, headers=None, cache_subdir=""):
    """
    Retrieves content from URL. Caches result to prevent repeated requests.
    """
    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
    target_dir = os.path.join(CACHE_DIR, cache_subdir) if cache_subdir else CACHE_DIR
    os.makedirs(target_dir, exist_ok=True)
    cache_path = os.path.join(target_dir, f"{url_hash}.{cache_type}")

    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()

    # Rate limiting sleep
    if sleep_sec > 0:
        time.sleep(sleep_sec)

    req_headers = {"User-Agent": UA}
    if headers:
        req_headers.update(headers)

    # Inject GitHub Token if querying github API
    if "api.github.com" in url:
        gh_token = CONFIG.get("github_token")
        if gh_token and gh_token != "YOUR_GITHUB_TOKEN":
            req_headers["Authorization"] = f"token {gh_token}"

    req = urllib.request.Request(url, data=post_data, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            content = response.read().decode("utf-8", errors="ignore")
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(content)
            return content
    except Exception as e:
        logging.error(f"Error fetching {url}: {e}")
        return None


class ProgressTracker:
    def __init__(self, venue_name, total):
        self.venue_name = venue_name
        self.total = total
        self.processed = 0
        self.successes = 0
        self.failures = 0
        self.start_time = time.time()

    def update(self, success, doi=None):
        self.processed += 1
        if success:
            self.successes += 1
        else:
            self.failures += 1
            elapsed = time.time() - self.start_time
            pct = (self.processed / self.total) * 100 if self.total else 100
            el_m, el_s = divmod(int(elapsed), 60)
            el_h, el_m = divmod(el_m, 60)
            elapsed_str = f"{el_h:02d}:{el_m:02d}:{el_s:02d}"
            logging.warning(
                f"[{self.venue_name}] No repository found for DOI/Repo: {doi} "
                f"(Progress: {self.processed}/{self.total}, {pct:.1f}%, Elapsed: {elapsed_str})"
            )

    def finalize(self):
        elapsed = time.time() - self.start_time
        pct = (self.processed / self.total) * 100 if self.total else 100
        speed = self.processed / elapsed if elapsed > 0 else 0
        el_m, el_s = divmod(int(elapsed), 60)
        el_h, el_m = divmod(el_m, 60)
        elapsed_str = f"{el_h:02d}:{el_m:02d}:{el_s:02d}"

        logging.info(
            f"\n=== [{self.venue_name}] Final Statistics ===\n"
            f"  Total processed: {self.processed}/{self.total} ({pct:.1f}%)\n"
            f"  Successful mappings: {self.successes}\n"
            f"  Missing repositories: {self.failures}\n"
            f"  Total time elapsed: {elapsed_str}\n"
            f"  Average speed: {speed:.2f} items/sec\n"
            f"========================================="
        )


def query_crossref_works(filter_query, limit=None, rows_per_page=100):
    """
    Queries Crossref works using offset pagination.
    """
    offset = 0
    items = []
    base_url = "https://api.crossref.org/works"
    total_results = None

    while True:
        rows = min(rows_per_page, limit - len(items)) if limit else rows_per_page
        if rows <= 0:
            break
        params = {
            "filter": filter_query,
            "rows": rows,
            "offset": offset
        }
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        content = get_cached_url(url, cache_type="json", sleep_sec=0.5, cache_subdir="crossref")
        if not content:
            break

        try:
            data = json.loads(content)
            if total_results is None:
                total_results = data.get("message", {}).get("total-results", 0)
            page_items = data.get("message", {}).get("items", [])
            if not page_items:
                break
            items.extend(page_items)
            total_target = min(limit, total_results) if limit is not None else total_results
            print(f"  Querying Crossref page (retrieved {len(items)}/{total_target})...")
            if len(page_items) < rows:
                break
            offset += len(page_items)
        except Exception as e:
            logging.error(f"Failed to parse Crossref response: {e}")
            break

    return items


def scrape_joss(limit=None):
    """
    Downloads JOSS articles from Crossref and fetches their JOSS API JSON metadata for software repository and archive links.
    """
    logging.info("[JOSS] Harvesting metadata and repository mappings (API)...")
    works = query_crossref_works("prefix:10.21105", limit=limit)
    # The prefix 10.21105 covers JOSS, JOSE (Open Source Education) and JCON. Filter only JOSS.
    joss_works = [w for w in works if w.get("DOI", "").lower().startswith("10.21105/joss.")]
    rows = []
    tracker = ProgressTracker("JOSS", len(joss_works))

    for idx, w in enumerate(joss_works):
        doi = w.get("DOI", "")
        title = (w.get("title") or [""])[0]
        published = w.get("published", {}).get("date-parts", [[None]])[0][0]
        year = published if published else ""

        repo_url = ""
        archive_url = ""

        # Fetch JOSS paper metadata in JSON format (non-aggressive API approach)
        joss_url = f"https://joss.theoj.org/papers/{doi}.json"
        content = get_cached_url(joss_url, cache_type="json", sleep_sec=0.1, cache_subdir="joss")
        if content:
            try:
                data = json.loads(content)
                repo_url = data.get("software_repository", "")
                archive_url = data.get("software_archive", "")
            except Exception as e:
                logging.error(f"Error parsing JOSS JSON for {doi}: {e}")

        has_repo = bool(repo_url or archive_url)
        tracker.update(has_repo, doi=doi)
        rows.append([doi, year, title, repo_url, archive_url])

    tracker.finalize()
    out_path = os.path.join(HERE, "joss_pairs.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["doi", "year", "title", "repo_url", "archive_url"])
        writer.writerows(rows)
    logging.info(f"  -> Saved {len(rows)} JOSS rows to {out_path}")
    return rows


def scrape_jors(limit=None):
    """
    Downloads JORS articles from Crossref and scrapes their publication pages for software repository links.
    """
    logging.info("[JORS] Harvesting metadata and scraping repositories...")
    # JORS ISSN is 2049-9647
    works = query_crossref_works("issn:2049-9647", limit=limit)
    rows = []
    tracker = ProgressTracker("JORS", len(works))

    for idx, w in enumerate(works):
        doi = w.get("DOI", "")
        title = (w.get("title") or [""])[0]
        published = w.get("published", {}).get("date-parts", [[None]])[0][0]
        year = published if published else ""

        repo_url = ""

        # Retrieve landing page URL from Crossref links
        landing_url = ""
        for link in w.get("link", []):
            if link.get("content-type") == "unspecified" or "doi.org" not in link.get("URL", ""):
                landing_url = link.get("URL")
                break
        if not landing_url:
            landing_url = w.get("resource", {}).get("primary", {}).get("URL", f"https://doi.org/{doi}")

        html = get_cached_url(landing_url, cache_type="html", sleep_sec=0.2, cache_subdir="jors")
        if html:
            try:
                soup = BeautifulSoup(html, "html.parser")
                # Scan all links on the JORS page matching CODE_HOST
                candidates = []
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    if CODE_HOST.search(href) and not any(x in href.lower() for x in ["share", "twitter", "facebook", "linkedin"]):
                        candidates.append(href)

                if candidates:
                    # Select the most likely repo URL (e.g. shortest or one matching DOI suffix)
                    repo_url = candidates[0]
            except Exception as e:
                logging.error(f"Error parsing HTML with BeautifulSoup for JORS DOI {doi}: {e}")

        has_repo = bool(repo_url)
        tracker.update(has_repo, doi=doi)
        rows.append([doi, year, title, repo_url])

    tracker.finalize()
    out_path = os.path.join(HERE, "jors_pairs.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["doi", "year", "title", "repo_url"])
        writer.writerows(rows)
    logging.info(f"  -> Saved {len(rows)} JORS rows to {out_path}")
    return rows


def scrape_softwarex(limit=None):
    """
    Scrapes SoftwareX repos from the official ElsevierSoftwareX GitHub organization
    and maps them to Crossref DOIs.
    """
    logging.info("[SoftwareX] Fetching SoftwareX works from Crossref...")
    works = query_crossref_works("issn:2352-7110", limit=limit)
    
    # Build a lookup dictionary of title/PII -> DOI
    pii_to_doi = {}
    title_to_doi = {}
    
    for w in works:
        doi = w.get("DOI", "")
        title = (w.get("title") or [""])[0]
        title_norm = re.sub(r"\W+", "", title.lower())
        title_to_doi[title_norm] = doi

        # Find PII in link or alternative-id
        pii = ""
        for alt in w.get("alternative-id", []):
            if alt.startswith("S2352-7110") or alt.startswith("S23527110"):
                pii = alt.replace("-", "").upper()
                break
        if not pii:
            for l in w.get("link", []):
                m = re.search(r"pii/(S2352\d{11}[0-9X])", l.get("URL", ""), re.I)
                if m:
                    pii = m.group(1).upper()
                    break
        if pii:
            pii_to_doi[pii] = doi

    logging.info(f"  Loaded {len(works)} SoftwareX works from Crossref. Mapping via GitHub...")

    # Fetch ElsevierSoftwareX repos
    gh_repos = []
    page = 1
    while True:
        url = f"https://api.github.com/orgs/ElsevierSoftwareX/repos?per_page=100&page={page}"
        logging.info(f"  Querying ElsevierSoftwareX GitHub repos page {page}...")
        content = get_cached_url(url, cache_type="json", sleep_sec=1.0, cache_subdir="softwarex")
        if not content:
            break
        try:
            page_repos = json.loads(content)
            if not page_repos:
                break
            gh_repos.extend(page_repos)
            if limit and len(gh_repos) >= limit:
                gh_repos = gh_repos[:limit]
                break
            page += 1
        except Exception as e:
            logging.error(f"Error parsing GitHub repos: {e}")
            break

    rows = []
    tracker = ProgressTracker("SoftwareX", len(gh_repos))
    for idx, repo in enumerate(gh_repos):
        repo_name = repo.get("name", "")
        desc = repo.get("description", "") or ""
        
        # We need the parent repo URL (the source fork)
        parent_url = ""
        repo_url = f"https://api.github.com/repos/ElsevierSoftwareX/{repo_name}"
        repo_detail_content = get_cached_url(repo_url, cache_type="json", sleep_sec=0.2, cache_subdir="softwarex")
        if repo_detail_content:
            try:
                repo_detail = json.loads(repo_detail_content)
                parent_url = repo_detail.get("parent", {}).get("html_url", "")
            except Exception:
                pass
        
        # Map back to DOI using PII or Title
        mapped_doi = ""
        # 1. Search description for PII
        pii_match = re.search(r"pii/(S2352\d{11}[0-9X])", desc, re.I)
        if pii_match:
            pii_val = pii_match.group(1).upper()
            mapped_doi = pii_to_doi.get(pii_val, "")
        
        # 2. Try matching normalized title
        if not mapped_doi:
            desc_cleaned = re.sub(r"to cite this software publication.*", "", desc, flags=re.I).strip()
            desc_norm = re.sub(r"\W+", "", desc_cleaned.lower())
            for t_norm, d_val in title_to_doi.items():
                # Match only when the repo description actually contains the
                # full (normalised) paper title. The previous reverse test
                # `desc_norm in t_norm` made an empty or tiny description match
                # the first title ('' is a substring of everything), collapsing
                # every PII-less mirror repo onto a single DOI. Require a
                # non-trivial title and the forward containment only.
                if len(t_norm) >= 10 and t_norm in desc_norm:
                    mapped_doi = d_val
                    break

        tracker.update(bool(mapped_doi), doi=mapped_doi if mapped_doi else f"ElsevierSoftwareX/{repo_name}")
        if mapped_doi:
            rows.append([mapped_doi, parent_url, repo.get("html_url", "")])

    tracker.finalize()
    out_path = os.path.join(HERE, "softwarex_pairs.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["doi", "repo_url", "elsevier_mirror_url"])
        writer.writerows(rows)
    logging.info(f"  -> Saved {len(rows)} SoftwareX rows to {out_path}")
    return rows


def scrape_ipol(limit=None):
    """
    Downloads IPOL articles from Crossref and scrapes their pages for code and SWH links.
    """
    logging.info("[IPOL] Harvesting metadata and scraping repositories...")
    # IPOL DOI prefix is 10.5201
    works = query_crossref_works("prefix:10.5201", limit=limit)
    rows = []
    tracker = ProgressTracker("IPOL", len(works))

    for idx, w in enumerate(works):
        doi = w.get("DOI", "")
        title = (w.get("title") or [""])[0]
        published = w.get("published", {}).get("date-parts", [[None]])[0][0]
        year = published if published else ""

        repo_url = ""
        archive_url = ""

        # Retrieve landing page URL from Crossref links or construct it
        landing_url = ""
        for link in w.get("link", []):
            if link.get("content-type") == "unspecified" or "ipol.im" in link.get("URL", ""):
                landing_url = link.get("URL")
                break
        if not landing_url:
            m = re.search(r"ipol\.(\d{4})\.(\d+)", doi.lower())
            if m:
                year_part, id_part = m.groups()
                landing_url = f"https://www.ipol.im/pub/art/{year_part}/{id_part}/"
            else:
                landing_url = w.get("resource", {}).get("primary", {}).get("URL", f"https://doi.org/{doi}")

        html = get_cached_url(landing_url, cache_type="html", sleep_sec=0.2, cache_subdir="ipol")
        if html:
            try:
                soup = BeautifulSoup(html, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    full_href = urllib.parse.urljoin(landing_url, href)
                    if "archive.softwareheritage.org" in href:
                        archive_url = full_href
                    elif not repo_url and any(href.lower().endswith(ext) for ext in [".tar.gz", ".zip", ".tgz", ".tar"]):
                        if "revisions" not in href.lower():
                            repo_url = full_href
            except Exception as e:
                logging.error(f"Error parsing HTML with BeautifulSoup for IPOL DOI {doi}: {e}")

        has_repo = bool(repo_url or archive_url)
        tracker.update(has_repo, doi=doi)
        rows.append([doi, year, title, repo_url, archive_url])

    tracker.finalize()
    out_path = os.path.join(HERE, "ipol_pairs.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["doi", "year", "title", "repo_url", "archive_url"])
        writer.writerows(rows)
    logging.info(f"  -> Saved {len(rows)} IPOL rows to {out_path}")
    return rows


def update_readme_stats():
    """
    Computes summary mapping statistics from the generated csv files
    and writes/updates them in README.md under a dedicated section.
    """
    readme_path = os.path.join(HERE, "README.md")
    if not os.path.exists(readme_path):
        return

    stats = {}
    targets = [
        ("joss_pairs.csv", "JOSS", [3, 4]),
        ("jors_pairs.csv", "JORS", [3]),
        ("softwarex_pairs.csv", "SoftwareX", [1, 2]),
        ("ipol_pairs.csv", "IPOL", [3, 4]),
        ("sigmod_pairs.csv", "SIGMOD", [3]),
    ]

    for filename, name, cols in targets:
        path = os.path.join(HERE, filename)
        if not os.path.exists(path):
            stats[name] = {"total": 0, "success": 0, "fail": 0, "pct": 0.0}
            continue
        try:
            total = 0
            success = 0
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)  # skip header
                for row in reader:
                    if not row:
                        continue
                    total += 1
                    is_success = False
                    for idx in cols:
                        if idx < len(row) and row[idx].strip():
                            is_success = True
                            break
                    if is_success:
                        success += 1
            fail = total - success
            pct = (success / total) * 100 if total > 0 else 0.0
            stats[name] = {"total": total, "success": success, "fail": fail, "pct": pct}
        except Exception as e:
            logging.error(f"Error computing stats for {filename}: {e}")
            stats[name] = {"total": 0, "success": 0, "fail": 0, "pct": 0.0}

    # Generate Markdown table
    table_lines = [
        "## Harvesting Statistics\n",
        "| Venue | Total Works | Successful Mappings | Missing Repositories | Success Rate |",
        "|---|---|---|---|---|",
    ]
    for name in ["JOSS", "JORS", "SoftwareX", "IPOL", "SIGMOD"]:
        s = stats[name]
        table_lines.append(f"| {name} | {s['total']} | {s['success']} | {s['fail']} | {s['pct']:.1f}% |")
    table_lines.append("\n")
    table_text = "\n".join(table_lines)

    # Read README and replace or append
    with open(readme_path, "r", encoding="utf-8") as f:
        readme_content = f.read()

    header_token = "## Harvesting Statistics"
    if header_token in readme_content:
        # Replace existing section
        pattern = re.compile(rf"{header_token}.*?(?=\n## |\Z)", re.DOTALL)
        new_content = pattern.sub(table_text.strip(), readme_content)
    else:
        # Append to end of file
        new_content = readme_content.rstrip() + "\n\n" + table_text

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    logging.info("Updated harvesting statistics in README.md")


def merge_all():
    """
    Merges newly scraped CSVs into `doi_repo_pairs.csv`.
    """
    logging.info("[merge] Re-building unified doi_repo_pairs.csv...")
    rows = []

    def load_rows(filename):
        path = os.path.join(HERE, filename)
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            return list(reader)

    # JOSS: doi, year, title, repo_url, archive_url
    for r in load_rows("joss_pairs.csv"):
        if len(r) >= 4 and r[3]:
            rows.append(["JOSS", r[0], "DOI", r[2], r[3], "Software repository"])

    # JORS: doi, year, title, repo_url
    for r in load_rows("jors_pairs.csv"):
        if len(r) >= 4 and r[3]:
            rows.append(["JORS", r[0], "DOI", r[2], r[3], "Software repository"])

    # SoftwareX: doi, repo_url, elsevier_mirror_url
    for r in load_rows("softwarex_pairs.csv"):
        if len(r) >= 2 and r[1]:
            rows.append(["SoftwareX", r[0], "DOI", "", r[1], "Software repository"])

    # IPOL: doi, year, title, repo_url, archive_url
    for r in load_rows("ipol_pairs.csv"):
        if len(r) >= 4:
            url = r[4] if (len(r) >= 5 and r[4]) else r[3]
            if url:
                rows.append(["IPOL", r[0], "DOI", r[2], url, "Software repository"])

    # SIGMOD: doi, year, title, repo_url, report_url
    for r in load_rows("sigmod_pairs.csv"):
        if len(r) >= 4 and r[3]:
            rows.append(["SIGMOD", r[0], "DOI", r[2], r[3], "Software repository"])

    # Load existing build_pairs results as fallback if file exists
    existing_pairs = os.path.join(HERE, "doi_repo_pairs.csv")
    existing_rows = []
    if os.path.exists(existing_pairs):
        with open(existing_pairs, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            existing_rows = list(reader)

    # Keep track of unique DOI -> Repo pairings
    seen = set()
    unified_rows = []

    for r in rows:
        key = (r[1], r[4])  # DOI, Repo
        if key not in seen:
            seen.add(key)
            unified_rows.append(r)

    # Only carry over sources this script does NOT re-scrape (the build_pairs.py
    # tracks: SoMeSci, Softcite, Wikidata). Rows for the re-harvested venues must
    # be fully REPLACED by the fresh scrape; otherwise stale/incorrect mappings
    # removed in this run would silently persist from the old file.
    REBUILT_VENUES = {"JOSS", "JORS", "SoftwareX", "IPOL", "SIGMOD"}
    for r in existing_rows:
        if len(r) >= 5 and r[0] not in REBUILT_VENUES:
            key = (r[1], r[4])
            if key not in seen:
                seen.add(key)
                unified_rows.append(r)

    out_path = os.path.join(HERE, "doi_repo_pairs.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["source", "publication_id", "id_type", "software", "repo_or_url", "context"])
        writer.writerows(unified_rows)

    logging.info(f"  -> Merged unified dataset: {len(unified_rows)} total rows saved to {out_path}")
    update_readme_stats()


def main():
    parser = argparse.ArgumentParser(description="Harvest software venues for DOI -> Repo mappings.")
    parser.add_argument("venues", nargs="*", default=["joss", "jors", "softwarex", "ipol", "sigmod"],
                        help="Venues to harvest: joss, jors, softwarex, ipol, sigmod")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max records to harvest per venue (useful for debugging)")
    parser.add_argument("--merge-only", action="store_true",
                        help="Only run the merge step without downloading new data")
    args = parser.parse_args()

    if args.merge_only:
        merge_all()
        return

    venues = [v.lower() for v in args.venues]

    if "joss" in venues:
        scrape_joss(limit=args.limit)
    if "jors" in venues:
        scrape_jors(limit=args.limit)
    if "softwarex" in venues:
        scrape_softwarex(limit=args.limit)
    if "ipol" in venues:
        scrape_ipol(limit=args.limit)
    if "sigmod" in venues:
        import harvest_sigmod
        harvest_sigmod.harvest_sigmod(limit=args.limit)

    merge_all()
    logging.info("done.")


if __name__ == "__main__":
    main()
