#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""gen_ipol_schemas.py -- derive the four IPOL OpenRefine/Wikibase schemas from the
JORS schemas. IPOL is a repo-less, SWHID-only venue, so the software schema drops
the whole P1324 statement group (and its GitHub/host qualifiers + reference) and
keeps only label/alias/desc, P31 software, and P6138 (native SWHID). Article and
cross-link schemas are the JORS ones with the venue swapped to Image Processing On
Line. Deterministic and reproducible (mirrors the JOSS->JORS schema transform).

Venue overrides:
  Q27725945 (Journal of Open Research Software) -> Q50815456 (Image Processing On Line)

Writes: ipol_schema_1_software.json, ipol_schema_2_article.json,
        ipol_schema_3a_article_p921.json, ipol_schema_3b_software_p1343.json
(no schema 1b: IPOL has 0 pre-existing software items, so no existing-SWHID pass.)
"""
import copy
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
IMPORT = os.path.join(HERE, "import")

JORS_VENUE_QID = "Q27725945"
IPOL_VENUE_QID = "Q50815456"
JORS_VENUE_NAME = "Journal of Open Research Software"
IPOL_VENUE_NAME = "Image Processing On Line"


def load(name):
    with open(os.path.join(IMPORT, name), encoding="utf-8") as f:
        return json.load(f)


def dump(obj, name):
    with open(os.path.join(IMPORT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f"  wrote {name}")


def swap_venue(obj):
    """Recursively swap the JORS venue QID/name for the IPOL one (in-place)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                if v == JORS_VENUE_QID:
                    obj[k] = IPOL_VENUE_QID
                elif JORS_VENUE_NAME in v:
                    obj[k] = v.replace(JORS_VENUE_NAME, IPOL_VENUE_NAME)
            else:
                swap_venue(v)
    elif isinstance(obj, list):
        for x in obj:
            swap_venue(x)
    return obj


def main():
    # ---- schema 1: software (drop the whole P1324 group; keep P31 + P6138) ----
    sw = load("jors_schema_1_software.json")
    edit = sw["entityEdits"][0]
    kept = [g for g in edit["statementGroups"]
            if g["property"]["pid"] != "P1324"]
    dropped = len(edit["statementGroups"]) - len(kept)
    edit["statementGroups"] = kept
    swap_venue(sw)  # swaps the venue in the P31 reference (stated in)
    assert dropped == 1, f"expected to drop exactly one P1324 group, dropped {dropped}"
    pids = [g["property"]["pid"] for g in edit["statementGroups"]]
    assert pids == ["P31", "P6138"], f"unexpected software statement groups: {pids}"
    dump(sw, "ipol_schema_1_software.json")

    # ---- schema 3a (venue swap) -- also the source of the referenced-P921 block ----
    art_p921 = swap_venue(load("jors_schema_3a_article_p921.json"))
    dump(art_p921, "ipol_schema_3a_article_p921.json")
    ipol_p921_refs = art_p921["entityEdits"][0]["statementGroups"][0]["statements"][0]["references"]

    # ---- schema 2: article (venue swap) + attach the IPOL reference to folded P921 --
    # Unlike JOSS/JORS (whose folded P921 was created bare and referenced later),
    # give the new-article P921 the same IPOL reference as the existing-article P921
    # (3a), so every P921 carries the venue evidence from the first upload.
    art = swap_venue(load("jors_schema_2_article.json"))
    # article description: read the per-row article_desc column (disambiguates the
    # one same-title new-article collision, g_igcs) instead of a fixed constant.
    for nd in art["entityEdits"][0]["nameDescs"]:
        if nd["name_type"] == "DESCRIPTION_IF_NEW":
            assert nd["value"]["value"]["type"] == "wbstringconstant"
            nd["value"]["value"] = {"type": "wbstringvariable",
                                    "columnName": "article_desc"}
    p921_groups = [g for g in art["entityEdits"][0]["statementGroups"]
                   if g["property"]["pid"] == "P921"]
    assert len(p921_groups) == 1, "expected exactly one P921 group in article schema"
    st = p921_groups[0]["statements"][0]
    assert st["references"] == [], "folded P921 should start bare"
    st["references"] = copy.deepcopy(ipol_p921_refs)
    dump(art, "ipol_schema_2_article.json")

    # ---- schema 3b: software P1343 (venue swap only) ----
    dump(swap_venue(load("jors_schema_3b_software_p1343.json")),
         "ipol_schema_3b_software_p1343.json")

    print(f"IPOL schemas generated (software P1324 group dropped; venue "
          f"{JORS_VENUE_QID} -> {IPOL_VENUE_QID}).")


if __name__ == "__main__":
    main()
