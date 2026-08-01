#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""
extract_repos.py  --  Extract, clean, and filter unique repository URLs from doi_repo_pairs.csv
and save them to repos_list.json.
"""
import csv
import json
import os
import re
import urllib.parse

PREPROC_DIR = os.path.dirname(os.path.abspath(__file__))
PAIRS_FILE = os.path.join(PREPROC_DIR, "doi_repo_pairs.csv")
REPOS_FILE = os.path.join(PREPROC_DIR, "repos_list.json")

# Regex to detect code-hosting platforms (same as build_pairs.py).
# "gitlab\." (not just gitlab.com) catches self-hosted GitLab instances such
# as gitlab.eurecom.fr; ipads.se.sjtu.edu.cn is SJTU IPADS' self-hosted
# GitLab (no "gitlab" in the hostname) hosting the WeTune SIGMOD artifact.
CODE_HOST = re.compile(
    r"(github\.com|gitlab\.|bitbucket\.org|sourceforge\.net|code\.google\.com|"
    r"savannah\.(non)?gnu\.org|gitorious|git\.|/git/|launchpad\.net|codeberg\.org|"
    r"ipads\.se\.sjtu\.edu\.cn)", re.I)

def clean_url(url):
    # Decode URL-encoded characters (like %0A, %09, %20)
    decoded = urllib.parse.unquote(url)
    # Remove all whitespace characters
    cleaned = re.sub(r"\s+", "", decoded)
    return cleaned

def main():
    if not os.path.exists(PAIRS_FILE):
        print(f"Error: {PAIRS_FILE} not found. Run build_pairs.py first.")
        return

    repos = set()
    with open(PAIRS_FILE, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # Skip header
        for row in reader:
            if len(row) >= 5:
                raw_url = row[4].strip()
                if not raw_url:
                    continue
                
                # Clean URL
                url = clean_url(raw_url)
                
                # Filter out existing SWH archive links and ensure it matches code hosts
                if "archive.softwareheritage.org" not in url:
                    if url.startswith("http://") or url.startswith("https://"):
                        if CODE_HOST.search(url):
                            repos.add(url)

    sorted_repos = [{"url": r} for r in sorted(repos)]
    
    with open(REPOS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted_repos, f, indent=4)
        
    print(f"Successfully extracted, cleaned, and filtered {len(sorted_repos)} unique repository URLs to {REPOS_FILE}")

if __name__ == "__main__":
    main()
