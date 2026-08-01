#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""
archive_individual.py  --  Archiving tool targeting individual save request endpoints on Software Heritage.
Based on Francesco Tosoni's MediaWiki Code2Code Search archiving script.
"""
import json
import os
import sys
import time
from urllib.parse import quote
import requests

PREPROC_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(PREPROC_DIR, "config.json")
REPOS_FILE = os.path.join(PREPROC_DIR, "repos_list.json")
LOG_FILE = os.path.join(PREPROC_DIR, "swh_individual_log.json")

def load_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"Error: {CONFIG_FILE} not found. Please create it first.")
        sys.exit(1)
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def archive_individual():
    config = load_config()
    visit_type = config.get("visit_type", "git")
    
    if not os.path.exists(REPOS_FILE):
        print(f"Error: {REPOS_FILE} not found. Run the extraction script first.")
        return

    with open(REPOS_FILE, "r") as f:
        repos = json.load(f)

    # Prepare authorization header if token is filled in, else try without token
    headers = {
        "User-Agent": config["user_agent"],
        "Accept": "application/json"
    }
    
    token = config.get("swh_token")
    if token and token != "YOUR_SWH_API_TOKEN":
        headers["Authorization"] = f"Bearer {token}"

    print(f"Total repositories to archive: {len(repos)}")
    print(f"Individual mode enabled (1 request per repo).\n")

    log_data = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            try:
                log_data = json.load(f)
            except json.JSONDecodeError:
                log_data = []

    # Map of processed URLs to avoid duplicates if resuming
    processed_urls = {entry["url"] for entry in log_data}

    for idx, repo in enumerate(repos, 1):
        url = repo["url"]
        if url in processed_urls:
            continue

        encoded_url = quote(url, safe='')
        save_url = f"https://archive.softwareheritage.org/api/1/origin/save/{visit_type}/url/{encoded_url}/"
        
        print(f"[{idx}/{len(repos)}] Archiving {url}...", end=" ", flush=True)

        try:
            # We don't need a JSON body for individual save via URL path
            response = requests.post(save_url, headers=headers, timeout=30)
            
            if response.status_code in [200, 201]:
                result = response.json()
                req_status = result.get("save_request_status", "accepted")
                task_status = result.get("save_task_status", "pending")
                print(f"[SUCCESS] (request: {req_status}, task: {task_status})")
                log_data.append({
                    "url": url,
                    "save_request_status": req_status,
                    "save_task_status": task_status,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "status": "success"
                })
            elif response.status_code == 429:
                print(f"[WARNING] Rate limited (429). Waiting 60 seconds...")
                time.sleep(60)
                continue
            else:
                print(f"[FAILED] ({response.status_code})")
                log_data.append({
                    "url": url,
                    "status_code": response.status_code,
                    "error": response.text,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "status": "failed"
                })
            
            # Periodically save log
            if idx % 10 == 0:
                with open(LOG_FILE, "w") as f:
                    json.dump(log_data, f, indent=4)

            # Small delay to respect rate limits
            time.sleep(0.5)

        except requests.exceptions.RequestException as e:
            print(f"❌ Error: {e}")
            break

    # Final save
    with open(LOG_FILE, "w") as f:
        json.dump(log_data, f, indent=4)

    print(f"\nIndividual archiving process completed. Log saved to {LOG_FILE}")

if __name__ == "__main__":
    archive_individual()
