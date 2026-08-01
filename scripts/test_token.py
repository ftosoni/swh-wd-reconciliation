# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
import json
import urllib.request
import urllib.error

with open("c:/Users/franc/Documents/SWH-Wikidata-thesis/datasets/pairs/config.json", "r") as f:
    config = json.load(f)

url = "https://archive.softwareheritage.org/api/1/origin/https://github.com/youngkaneda/DemeterWatch/visits/"
headers = {
    "User-Agent": config["user_agent"],
    "Authorization": f"Bearer {config['swh_token']}"
}

req = urllib.request.Request(url, headers=headers)
try:
    with urllib.request.urlopen(req) as r:
        print("Status code:", r.status)
        for h, v in r.headers.items():
            if "ratelimit" in h.lower():
                print(f"{h}: {v}")
except urllib.error.HTTPError as e:
    print("HTTPError:", e.code)
    for h, v in e.headers.items():
        if "ratelimit" in h.lower():
            print(f"{h}: {v}")
except Exception as e:
    print("Error:", e)
