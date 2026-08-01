#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""
retrieve_swhids.py  --  Retrieve Software Heritage Identifiers (SWHIDs)
for repositories listed in doi_repo_pairs.csv, matching the publication year if possible.
"""
import csv
import datetime
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request

# Path handling relative to script location
PREPROC_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(PREPROC_DIR, "config.json")
PAIRS_FILE = os.path.join(PREPROC_DIR, "doi_repo_pairs.csv")
SWH_API_BASE = "https://archive.softwareheritage.org/api/1"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(PREPROC_DIR, "retrieve_swhids.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)


def load_config():
    if not os.path.exists(CONFIG_FILE):
        logging.warning(f"Config file {CONFIG_FILE} not found. Please copy 'config.json.template' to 'config.json' and fill in your token. Running without authorization token (slower rate limits).")
        return {}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def load_publication_years():
    """Build a lookup dictionary of DOI -> Year from the source venue CSV files."""
    doi_years = {}
    filenames = ["joss_pairs.csv", "jors_pairs.csv", "ipol_pairs.csv", "softwarex_dois.csv"]
    for fname in filenames:
        path = os.path.join(PREPROC_DIR, fname)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    header = next(reader, None)
                    for row in reader:
                        if len(row) >= 2 and row[0] and row[1]:
                            try:
                                doi_years[row[0].strip().lower()] = int(row[1].strip())
                            except ValueError:
                                pass
            except Exception as e:
                logging.error(f"Error loading {fname}: {e}")
    return doi_years


def swh_api_get(endpoint, config):
    url = f"{SWH_API_BASE}/{endpoint.lstrip('/')}"
    headers = {}
    if "user_agent" in config:
        headers["User-Agent"] = config["user_agent"]
    if "swh_token" in config:
        headers["Authorization"] = f"Bearer {config['swh_token']}"

    max_retries = 10
    base_backoff = 2
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            # Faster pacing, as requested. Backoff or reset sleep will handle rate limiting
            time.sleep(0.1)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8")), 200, None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                reset_time = e.headers.get("X-Ratelimit-Reset")
                retry_after = e.headers.get("Retry-After")
                
                if reset_time:
                    try:
                        sleep_time = int(reset_time) - int(time.time())
                        sleep_time = max(sleep_time, 0) + 5  # 5s buffer
                        reset_dt = datetime.datetime.fromtimestamp(int(reset_time))
                        logging.warning(f"Rate limit (429) hit on {url}. Quota resets at {reset_dt}. Sleeping for {sleep_time} seconds (attempt {attempt+1}/{max_retries})...")
                    except Exception:
                        sleep_time = base_backoff ** attempt
                        logging.warning(f"Rate limit (429) hit on {url}. Sleeping for {sleep_time} seconds (attempt {attempt+1}/{max_retries})...")
                elif retry_after:
                    try:
                        sleep_time = int(retry_after) + 2
                        logging.warning(f"Rate limit (429) hit on {url}. Retry-After specifies {retry_after}s. Sleeping for {sleep_time} seconds (attempt {attempt+1}/{max_retries})...")
                    except Exception:
                        sleep_time = base_backoff ** attempt
                        logging.warning(f"Rate limit (429) hit on {url}. Sleeping for {sleep_time} seconds (attempt {attempt+1}/{max_retries})...")
                else:
                    sleep_time = base_backoff ** attempt
                    logging.warning(f"Rate limit (429) hit on {url}. Sleeping for {sleep_time} seconds (attempt {attempt+1}/{max_retries})...")
                
                time.sleep(sleep_time)
                continue
            
            error_msg = e.read().decode("utf-8", errors="ignore")
            return None, e.code, f"HTTPError {e.code}: {e.reason} ({error_msg})"
        except Exception as e:
            return None, None, str(e)
            
    return None, 429, "Max retries reached on rate limit (429)"


def select_best_visit(visits, pub_year):
    """
    Selects the successful visit closest to or immediately after the publication year.
    Fallback to the latest successful visit.
    """
    successful_visits = []
    for v in visits:
        if v.get("status") == "full" and v.get("snapshot"):
            date_str = v.get("date")
            try:
                # ISO date parsing e.g. "2021-05-25T14:32:00Z"
                dt = datetime.datetime.strptime(date_str[:10], "%Y-%m-%d")
                successful_visits.append((dt, v))
            except Exception:
                pass

    if not successful_visits:
        return None

    # Sort successful visits chronologically
    successful_visits.sort(key=lambda x: x[0])

    if pub_year:
        # Find first visit in or after the publication year
        for dt, v in successful_visits:
            if dt.year >= pub_year:
                return v
        # If all visits were before the pub year, return the last one (closest to pub year)
        return successful_visits[-1][1]

    # Default to latest visit
    return successful_visits[-1][1]


def resolve_revision_id(snapshot_id, config, snapshot_cache):
    """
    Fetches snapshot details and tries to extract the revision hash (SHA1)
    for master, main, or release branches.
    """
    if snapshot_id in snapshot_cache:
        return snapshot_cache[snapshot_id]

    snap_data, status_code, err = swh_api_get(f"snapshot/{snapshot_id}/", config)
    if not snap_data or "branches" not in snap_data:
        snapshot_cache[snapshot_id] = None
        return None

    branches = snap_data.get("branches", {})
    # Prioritized branch names
    priority_branches = [
        "refs/heads/main",
        "refs/heads/master",
        "refs/heads/trunk",
        "refs/heads/production",
        "HEAD"
    ]

    rev_id = None
    for bname in priority_branches:
        if bname in branches and branches[bname]:
            target = branches[bname]
            if target.get("target_type") == "revision":
                rev_id = target['target']
                break
            elif target.get("target_type") == "alias":
                alias_name = target.get("target")
                if alias_name in branches and branches[alias_name] and branches[alias_name].get("target_type") == "revision":
                    rev_id = branches[alias_name]['target']
                    break

    # Fallback: check any tag or release branch
    if not rev_id:
        for bname, target in branches.items():
            if target and target.get("target_type") == "revision":
                # If it starts with refs/tags/
                if bname.startswith("refs/tags/"):
                    rev_id = target['target']
                    break

    snapshot_cache[snapshot_id] = rev_id
    return rev_id


def get_directory_for_revision(revision_id, config, revision_cache):
    """
    Queries the revision endpoint to find the directory ID associated with it.
    """
    if revision_id in revision_cache:
        return revision_cache[revision_id]

    rev_data, status_code, err = swh_api_get(f"revision/{revision_id}/", config)
    if rev_data and "directory" in rev_data:
        directory_id = rev_data["directory"]
        revision_cache[revision_id] = directory_id
        return directory_id

    revision_cache[revision_id] = None
    return None


def main():
    config = load_config()
    pub_years = load_publication_years()

    if not os.path.exists(PAIRS_FILE):
        logging.error(f"{PAIRS_FILE} not found. Please run harvest_venues.py or build_pairs.py first.")
        return

    # Load unified pairs
    pairs = []
    with open(PAIRS_FILE, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        pairs = list(reader)

    logging.info(f"Loaded {len(pairs)} publication-repository pairs from {PAIRS_FILE}")

    output_rows = []
    resolved_count = 0

    log_file = os.path.join(PREPROC_DIR, "swhid_resolution_log.json")
    resolution_log = {}

    # Load existing log to resume
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                resolution_log = json.load(f)
            logging.info(f"Loaded existing resolution log with {len(resolution_log)} entries to resume progress.")
        except Exception as e:
            logging.error(f"Error loading existing log {log_file}: {e}. Starting fresh.")

    visits_cache = {}
    snapshot_cache = {}
    revision_cache = {}
    new_queries_count = 0

    for idx, row in enumerate(pairs):
        if len(row) < 5:
            continue
        source, pub_id, id_type, software, repo_url, context = row[0], row[1], row[2], row[3], row[4], row[5]

        # Check if repo_url is populated and is not already a SWHID
        if not repo_url or "archive.softwareheritage.org" in repo_url or repo_url.startswith("swh:1:"):
            # Keep as is
            output_rows.append(row)
            continue

        # Check if we can resume from the loaded log
        if repo_url in resolution_log:
            entry = resolution_log[repo_url]
            if entry.get("status") == "success":
                swhid = entry.get("swhid")
                resolved_count += 1
                output_rows.append([source, pub_id, id_type, software, swhid, f"SWHID resolved from {repo_url}"])
            else:
                reason = entry.get("reason", "")
                if "404" in reason or "403" in reason or "empty" in reason or "No successful visit" in reason:
                    output_rows.append(row)
                else:
                    # Retry transient failures
                    logging.info(f"[{idx+1}/{len(pairs)}] Retrying query for: {repo_url} ...")
                    new_queries_count += 1
                    # Execute resolution logic
                    swhid = None
                    # Quoted origin URL
                    quoted_origin = urllib.parse.quote(repo_url, safe="")
                    visits, status_code, err_msg = swh_api_get(f"origin/{quoted_origin}/visits/", config)
                    if visits is None:
                        visits_err = f"API Error: {status_code} - {err_msg}" if status_code else f"Network/Connection Error: {err_msg}"
                    else:
                        visits_err = None

                    if visits:
                        pub_year = pub_years.get(pub_id.lower())
                        best_visit = select_best_visit(visits, pub_year)
                        if best_visit and best_visit.get("snapshot"):
                            snap_id = best_visit["snapshot"]
                            rev_id = resolve_revision_id(snap_id, config, snapshot_cache)
                            if rev_id:
                                dir_id = get_directory_for_revision(rev_id, config, revision_cache)
                                if dir_id:
                                    swhid = f"swh:1:dir:{dir_id};origin={repo_url};visit=swh:1:snp:{snap_id};anchor=swh:1:rev:{rev_id}"
                            if not swhid:
                                swhid = f"swh:1:snp:{snap_id};origin={repo_url};visit=swh:1:snp:{snap_id}"
                        
                        successful_visits = [v for v in visits if v.get("status") == "full" and v.get("snapshot")]
                        if swhid:
                            resolution_log[repo_url] = {
                                "status": "success",
                                "swhid": swhid,
                                "visits_count": len(visits),
                                "successful_visits_count": len(successful_visits),
                                "reason": "Resolved successfully with qualifiers"
                            }
                            resolved_count += 1
                            output_rows.append([source, pub_id, id_type, software, swhid, f"SWHID resolved from {repo_url}"])
                        else:
                            resolution_log[repo_url] = {
                                "status": "failed",
                                "visits_count": len(visits),
                                "successful_visits_count": len(successful_visits),
                                "reason": "No successful visit found containing a snapshot"
                            }
                            output_rows.append(row)
                    else:
                        if visits_err:
                            resolution_log[repo_url] = {"status": "failed", "reason": visits_err}
                        else:
                            resolution_log[repo_url] = {"status": "failed", "visits_count": 0, "successful_visits_count": 0, "reason": "SWH origin visits list is empty"}
                        output_rows.append(row)

                    if new_queries_count % 20 == 0:
                        with open(log_file, "w", encoding="utf-8") as f:
                            json.dump(resolution_log, f, indent=4)
                        logging.info(f"Progress saved (checkpoint) at new query {new_queries_count}")
            continue

        # New URL query
        logging.info(f"[{idx+1}/{len(pairs)}] Querying SWHID for: {repo_url} ...")
        new_queries_count += 1

        # Check if visits are already in visits_cache
        if repo_url in visits_cache:
            visits, visits_err = visits_cache[repo_url]
        else:
            quoted_origin = urllib.parse.quote(repo_url, safe="")
            visits, status_code, err_msg = swh_api_get(f"origin/{quoted_origin}/visits/", config)
            if visits is None:
                visits_err = f"API Error: {status_code} - {err_msg}" if status_code else f"Network/Connection Error: {err_msg}"
            else:
                visits_err = None
            visits_cache[repo_url] = (visits, visits_err)

        swhid = None
        if visits:
            pub_year = pub_years.get(pub_id.lower())
            best_visit = select_best_visit(visits, pub_year)

            if best_visit and best_visit.get("snapshot"):
                snap_id = best_visit["snapshot"]
                rev_id = resolve_revision_id(snap_id, config, snapshot_cache)
                if rev_id:
                    dir_id = get_directory_for_revision(rev_id, config, revision_cache)
                    if dir_id:
                        swhid = f"swh:1:dir:{dir_id};origin={repo_url};visit=swh:1:snp:{snap_id};anchor=swh:1:rev:{rev_id}"

                if not swhid:
                    swhid = f"swh:1:snp:{snap_id};origin={repo_url};visit=swh:1:snp:{snap_id}"

            successful_visits = [v for v in visits if v.get("status") == "full" and v.get("snapshot")]
            if swhid:
                resolution_log[repo_url] = {
                    "status": "success",
                    "swhid": swhid,
                    "visits_count": len(visits),
                    "successful_visits_count": len(successful_visits),
                    "reason": "Resolved successfully with qualifiers"
                }
            else:
                resolution_log[repo_url] = {
                    "status": "failed",
                    "visits_count": len(visits),
                    "successful_visits_count": len(successful_visits),
                    "reason": "No successful visit found containing a snapshot"
                }
        else:
            err_detail = visits_cache[repo_url][1]
            if err_detail:
                resolution_log[repo_url] = {
                    "status": "failed",
                    "reason": err_detail
                }
            else:
                resolution_log[repo_url] = {
                    "status": "failed",
                    "visits_count": 0,
                    "successful_visits_count": 0,
                    "reason": "SWH origin visits list is empty"
                }

        if swhid:
            resolved_count += 1
            logging.info(f"  -> Resolved SWHID: {swhid}")
            output_rows.append([source, pub_id, id_type, software, swhid, f"SWHID resolved from {repo_url}"])
        else:
            logging.warning(f"  -> Resolution failed for {repo_url}")
            output_rows.append(row)

        # Periodically save log
        if new_queries_count % 20 == 0:
            try:
                with open(log_file, "w", encoding="utf-8") as f:
                    json.dump(resolution_log, f, indent=4)
                logging.info(f"Progress saved (checkpoint) at new query {new_queries_count}")
            except Exception as e:
                logging.error(f"Error saving progress checkpoint: {e}")

    # Final save of log
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(resolution_log, f, indent=4)
        logging.info(f"Resolution statistics log final save completed to {log_file}")
    except Exception as e:
        logging.error(f"Error doing final save of resolution log: {e}")

    # Calculate summary statistics
    total_unique = len(resolution_log)
    total_success = sum(1 for item in resolution_log.values() if item.get("status") == "success")
    total_failed = total_unique - total_success

    failure_reasons = {}
    for item in resolution_log.values():
        if item.get("status") == "failed":
            reason = item.get("reason", "Unknown error")
            if "HTTPError 404" in reason:
                reason = "SWH Origin Not Found (404)"
            elif "HTTPError 403" in reason:
                reason = "SWH Forbidden (403)"
            elif "HTTPError 429" in reason:
                reason = "SWH Rate Limited (429)"
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

    logging.info("=== SWHID Resolution Statistics ===")
    logging.info(f"Total unique repository URLs: {total_unique}")
    logging.info(f"Successfully resolved to SWHID: {total_success} ({total_success/total_unique*100:.2f}%)" if total_unique > 0 else "Successfully resolved to SWHID: 0")
    logging.info(f"Failed to resolve: {total_failed} ({total_failed/total_unique*100:.2f}%)" if total_unique > 0 else "Failed to resolve: 0")
    logging.info("Failure reasons breakdown:")
    for reason, count in sorted(failure_reasons.items(), key=lambda x: x[1], reverse=True):
        logging.info(f"  - {reason}: {count} ({count/total_unique*100:.2f}%)" if total_unique > 0 else f"  - {reason}: {count}")

    # Save outputs to a new file to prevent overwriting raw URLs
    resolved_file = os.path.join(PREPROC_DIR, "doi_swhid_pairs.csv")
    with open(resolved_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["source", "publication_id", "id_type", "software", "repo_or_url", "context"])
        writer.writerows(output_rows)

    logging.info(f"Done. Successfully resolved {resolved_count} SWHIDs. Saved to {resolved_file}")


if __name__ == "__main__":
    main()
