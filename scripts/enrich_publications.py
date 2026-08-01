#!/usr/bin/env python3
# Copyright (c) 2026 Francesco Tosoni
# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of swh-wd-reconciliation
# (https://github.com/ftosoni/swh-wd-reconciliation).
# Licensed under the BSD 3-Clause License; see the LICENSE file for details.
"""
enrich_publications.py  --  Enrich the publication-repository pairs dataset 
with bibliographic metadata from Crossref and NCBI APIs, generating a 
comprehensive JSON file for OpenRefine.
"""
import csv
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# Setup directories
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")
CROSSREF_CACHE = os.path.join(CACHE_DIR, "crossref_metadata")
NCBI_CACHE = os.path.join(CACHE_DIR, "ncbi_conversions")
os.makedirs(CROSSREF_CACHE, exist_ok=True)
os.makedirs(NCBI_CACHE, exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(HERE, "enrich_publications.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

# Load config
CONFIG_FILE = os.path.join(HERE, "config.json")
CONFIG = {}
if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            CONFIG = json.load(f)
    except Exception as e:
        logging.warning(f"Failed to load config.json: {e}")

UA = CONFIG.get("user_agent", "SWH-Wikidata-thesis-enricher/1.0 (academic research; mailto:Francesco.Tosoni@santannapisa.it)")


def resolve_pmcid_to_doi(pmcid):
    """
    Queries the NCBI ID Converter API to resolve a PMCID (e.g., PMC2811132) to a DOI.
    """
    pmcid_clean = pmcid.strip().upper()
    if not pmcid_clean.startswith("PMC"):
        pmcid_clean = f"PMC{pmcid_clean}"

    cache_path = os.path.join(NCBI_CACHE, f"{pmcid_clean}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("doi")
        except Exception:
            pass

    url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?tool=SWH-Wikidata-thesis&email=Francesco.Tosoni@santannapisa.it&ids={pmcid_clean}&format=json"
    req = urllib.request.Request(url, headers={"User-Agent": UA})

    try:
        time.sleep(0.3)  # Rate limiting NCBI (max 3 req/sec without API key)
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode("utf-8"))
            records = res.get("records", [])
            if records:
                doi = records[0].get("doi")
                # Cache conversion
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump({"pmcid": pmcid_clean, "doi": doi}, f, indent=4)
                return doi
    except Exception as e:
        logging.error(f"Error converting PMCID {pmcid_clean} to DOI: {e}")

    return None


def fetch_crossref_metadata(doi):
    """
    Fetches work metadata from Crossref API using DOI, returning raw JSON object.
    Caches outputs to avoid repeated requests.
    """
    doi_clean = doi.strip().lower()
    # Safe filename for DOI
    safe_filename = urllib.parse.quote(doi_clean, safe="").replace("%", "_")
    cache_path = os.path.join(CROSSREF_CACHE, f"{safe_filename}.json")

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi_clean)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})

    try:
        time.sleep(0.5)  # Crossref polite pool rate limiting
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode("utf-8"))
            message = res.get("message", {})
            # Cache metadata
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(message, f, indent=4)
            return message
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logging.warning(f"Crossref record not found (404) for DOI: {doi_clean}")
            # Cache negative result to avoid retrying
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=4)
        else:
            logging.error(f"HTTP Error {e.code} fetching Crossref for DOI {doi_clean}: {e.reason}")
    except Exception as e:
        logging.error(f"Error fetching Crossref metadata for DOI {doi_clean}: {e}")

    return None


def fetch_datacite_metadata(doi):
    """
    Fetches work metadata from DataCite API using DOI, returning raw JSON object.
    Caches outputs to avoid repeated requests.
    """
    doi_clean = doi.strip().lower()
    safe_filename = urllib.parse.quote(doi_clean, safe="").replace("%", "_")
    datacite_cache = os.path.join(CACHE_DIR, "datacite_metadata")
    os.makedirs(datacite_cache, exist_ok=True)
    cache_path = os.path.join(datacite_cache, f"{safe_filename}.json")

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    url = f"https://api.datacite.org/dois/{urllib.parse.quote(doi_clean)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})

    try:
        time.sleep(0.5)  # Respect rate limits
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode("utf-8"))
            data = res.get("data", {})
            # Cache metadata
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            return data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logging.warning(f"DataCite record not found (404) for DOI: {doi_clean}")
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=4)
        else:
            logging.error(f"HTTP Error {e.code} fetching DataCite for DOI {doi_clean}: {e.reason}")
    except Exception as e:
        logging.error(f"Error fetching DataCite metadata for DOI {doi_clean}: {e}")

    return None


def parse_crossref_metadata(msg):
    """
    Extracts relevant ontological fields from Crossref work metadata.
    """
    if not msg:
        return {}

    title = ""
    titles = msg.get("title", [])
    if titles:
        title = titles[0]

    venue = ""
    venues = msg.get("container-title", [])
    if venues:
        venue = venues[0]

    # Handle publication date
    pub_date = ""
    pub_year = None
    date_parts = msg.get("published", {}).get("date-parts", [[None]])[0]
    if not date_parts or date_parts[0] is None:
        # Fallback to published-online or published-print
        date_parts = msg.get("published-online", {}).get("date-parts", [[None]])[0]
        if not date_parts or date_parts[0] is None:
            date_parts = msg.get("published-print", {}).get("date-parts", [[None]])[0]

    if date_parts and date_parts[0] is not None:
        pub_year = date_parts[0]
        parts = [str(x).zfill(2) for x in date_parts if x is not None]
        pub_date = "-".join(parts)

    publisher = msg.get("publisher", "")
    issns = msg.get("ISSN", [])

    authors = []
    for a in msg.get("author", []):
        authors.append({
            "given_name": a.get("given", ""),
            "family_name": a.get("family", ""),
            "orcid": a.get("ORCID", "")
        })

    return {
        "title": title,
        "venue": venue,
        "publication_date": pub_date,
        "publication_year": pub_year,
        "publisher": publisher,
        "issns": issns,
        "authors": authors
    }


def parse_datacite_metadata(data):
    """
    Extracts relevant ontological fields from DataCite work metadata.
    """
    if not data:
        return {}
    
    attrs = data.get("attributes", {})
    
    title = ""
    titles = attrs.get("titles", [])
    if titles:
        title = titles[0].get("title", "")

    # For DataCite (Zenodo), publisher is usually the venue
    venue = attrs.get("publisher", "")
    publisher = attrs.get("publisher", "")
    
    pub_year = attrs.get("publicationYear")
    
    pub_date = attrs.get("published", attrs.get("created", ""))
    if pub_date and len(pub_date) > 10:
        pub_date = pub_date[:10]
        
    authors = []
    for c in attrs.get("creators", []):
        orcid = ""
        for nid in c.get("nameIdentifiers", []):
            if nid.get("nameIdentifierScheme") == "ORCID":
                orcid = nid.get("nameIdentifier", "")
        
        authors.append({
            "given_name": c.get("givenName", ""),
            "family_name": c.get("familyName", ""),
            "orcid": orcid
        })

    return {
        "title": title,
        "venue": venue,
        "publication_date": pub_date,
        "publication_year": pub_year,
        "publisher": publisher,
        "issns": [],
        "authors": authors
    }


def load_resolved_swhids():
    """
    Reads SWHIDs from swhid_resolution_log.json and doi_swhid_pairs.csv
    and constructs a lookup dictionary of repo_url -> swhid.
    """
    swhids = {}
    
    # 1. Read from swhid_resolution_log.json (checkpoint log)
    log_file = os.path.join(HERE, "swhid_resolution_log.json")
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                log_data = json.load(f)
                for url, entry in log_data.items():
                    if entry.get("status") == "success" and entry.get("swhid"):
                        swhids[url] = entry["swhid"]
        except Exception as e:
            logging.error(f"Error loading resolution log: {e}")

    # 2. Read from doi_swhid_pairs.csv (overwrites/complements checkpoint log)
    csv_file = os.path.join(HERE, "doi_swhid_pairs.csv")
    if os.path.exists(csv_file):
        try:
            with open(csv_file, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)  # skip header
                for row in reader:
                    if len(row) >= 5:
                        repo_url = row[4].strip()
                        # If the cell itself is already a SWHID
                        if repo_url.startswith("swh:1:"):
                            # Find matching original repo URL from context if possible
                            ctx = row[5]
                            m = re.search(r"SWHID resolved from (.+)", ctx)
                            if m:
                                orig_url = m.group(1).strip()
                                swhids[orig_url] = repo_url
                            else:
                                swhids[repo_url] = repo_url
        except Exception as e:
            logging.error(f"Error loading resolved CSV: {e}")

    return swhids


def main():
    import argparse
    import collections
    parser = argparse.ArgumentParser(description="Enrich publication pairs with Crossref metadata.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of records to process")
    args = parser.parse_args()

    pairs_file = os.path.join(HERE, "doi_repo_pairs.csv")
    if not os.path.exists(pairs_file):
        logging.error(f"{pairs_file} not found. Please run build_pairs.py or harvest_venues.py first.")
        sys.exit(1)

    logging.info(f"Loading publication-repository pairs from {pairs_file}...")
    pairs = []
    with open(pairs_file, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        pairs = list(reader)

    if args.limit:
        logging.info(f"Limiting processing to the first {args.limit} records.")
        pairs = pairs[:args.limit]

    logging.info(f"Loaded {len(pairs)} records. Fetching SWHIDs...")
    swhids_lookup = load_resolved_swhids()
    logging.info(f"Loaded {len(swhids_lookup)} resolved SWHIDs for lookup.")

    # Load SIGMOD report URLs
    sigmod_reports = {}
    sigmod_file = os.path.join(HERE, "sigmod_pairs.csv")
    if os.path.exists(sigmod_file):
        with open(sigmod_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 5:
                    sigmod_reports[row[0]] = row[4]

    # Load SoftwareX mirror repo URLs: for each DOI, the ElsevierSoftwareX mirror
    # repository whose GitHub description carries the paper PII. That description
    # is the actual evidence for the paper<->repository link (the PII is the join
    # key against Crossref; see README), so it is the precise reference URL for
    # the P1324 repository statement, rather than the org-level listing endpoint.
    softwarex_mirrors = {}
    softwarex_file = os.path.join(HERE, "softwarex_pairs.csv")
    if os.path.exists(softwarex_file):
        with open(softwarex_file, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                doi_key = (row.get("doi") or "").strip()
                mirror = (row.get("elsevier_mirror_url") or "").strip()
                if doi_key and mirror:
                    softwarex_mirrors[doi_key] = mirror

    enriched_data_by_source = collections.defaultdict(list)
    total = len(pairs)
    
    # Track statistics
    resolved_dois = 0
    resolved_metadata = 0
    
    # Pinned to the original harvest date. The Crossref/GitHub/SWH data all come
    # from caches populated during that harvest, so this is the honest retrieval
    # date; re-running the mapping logic later must not drift it to "today".
    retrieved_date = "18 June 2026"

    for idx, row in enumerate(pairs, 1):
        if len(row) < 5:
            continue
        source, pub_id, id_type, software, repo_url, context = row[0], row[1], row[2], row[3], row[4], row[5]

        logging.info(f"[{idx}/{total}] Processing {source}: {id_type} {pub_id} ({software})...")

        # Resolve DOI if PMCID
        doi = None
        if id_type == "DOI":
            doi = pub_id
        elif id_type == "PMCID":
            doi = resolve_pmcid_to_doi(pub_id)
            if doi:
                resolved_dois += 1
                logging.info(f"  -> Resolved PMCID {pub_id} to DOI {doi}")
            else:
                logging.warning(f"  -> Could not resolve PMCID {pub_id} to DOI")

        # Query Crossref with fallback to DataCite
        crossref_raw = {}
        datacite_raw = {}
        metadata = {}
        is_datacite = False
        
        if doi:
            # Zenodo DOIs start with 10.5281/
            if doi.strip().lower().startswith("10.5281/"):
                is_datacite = True
            
            if not is_datacite:
                crossref_raw = fetch_crossref_metadata(doi)
                if crossref_raw:
                    resolved_metadata += 1
                    metadata = parse_crossref_metadata(crossref_raw)
                    logging.info(f"  -> Successfully fetched Crossref metadata: '{metadata.get('title')}'")
                else:
                    # Fallback to DataCite if Crossref failed
                    is_datacite = True
            
            if is_datacite:
                datacite_raw = fetch_datacite_metadata(doi)
                if datacite_raw:
                    resolved_metadata += 1
                    metadata = parse_datacite_metadata(datacite_raw)
                    logging.info(f"  -> Successfully fetched DataCite metadata: '{metadata.get('title')}'")

        # Lookup SWHID (fall back to raw repo URL if not resolved yet)
        swhid = swhids_lookup.get(repo_url)
        if not swhid:
            # Check if repo_url is already a SWHID
            if repo_url.startswith("swh:1:"):
                swhid = repo_url
                
        # Build provenance
        biblio_prov = {}
        if is_datacite and datacite_raw:
            biblio_prov = {
                "stated_in": "DataCite",
                "doi": doi,
                "reference_url": f"https://api.datacite.org/dois/{urllib.parse.quote(doi)}",
                "retrieved": retrieved_date
            }
        elif not is_datacite and crossref_raw:
            biblio_prov = {
                "stated_in": "Crossref",
                "doi": doi,
                "reference_url": f"https://api.crossref.org/works/{urllib.parse.quote(doi)}",
                "retrieved": retrieved_date
            }
            
        repo_prov = {}
        if source == "JOSS":
            repo_prov = {
                "stated_in": "Journal of Open Source Software API",
                "reference_url": f"https://joss.theoj.org/papers/{doi}.json" if doi else "",
                "retrieved": retrieved_date
            }
        elif source == "JORS":
            repo_prov = {
                "stated_in": "Journal of Open Research Software",
                "reference_url": f"https://doi.org/{doi}" if doi else "",
                "retrieved": retrieved_date
            }
        elif source == "SoftwareX":
            repo_prov = {
                "stated_in": "ElsevierSoftwareX GitHub Organization",
                "reference_url": softwarex_mirrors.get(doi)
                    or "https://api.github.com/orgs/ElsevierSoftwareX/repos",
                "retrieved": retrieved_date
            }
        elif source == "IPOL":
            repo_prov = {
                "stated_in": "Image Processing On Line",
                "reference_url": f"https://doi.org/{doi}" if doi else "",
                "retrieved": retrieved_date
            }
        elif source == "SIGMOD":
            report_url = sigmod_reports.get(doi, "")
            repo_prov = {
                "stated_in": "SIGMOD Availability & Reproducibility Initiative",
                "reference_url": report_url,
                "retrieved": retrieved_date
            }
        elif source == "SoMeSci":
            repo_prov = {
                "stated_in": "SoMeSci dataset",
                "reference_url": "https://zenodo.org/records/4701764",
                "retrieved": retrieved_date
            }
        elif source == "Softcite":
            repo_prov = {
                "stated_in": "Softcite dataset",
                "reference_url": "https://github.com/softcite/softcite_dataset_v2",
                "retrieved": retrieved_date
            }
        elif source == "Wikidata":
            repo_prov = {
                "stated_in": "Wikidata",
                "reference_url": "https://query.wikidata.org/sparql",
                "retrieved": retrieved_date
            }

        # Build comprehensive item
        item = {
            "publication_id": pub_id,
            "id_type": id_type,
            "resolved_doi": doi,
            "source": source,
            "software_name": software,
            "repo_url": repo_url,
            "swhid": swhid,
            "reconciliation_context": context,
            "provenance": {
                "bibliographic_metadata": biblio_prov,
                "repository_mapping": repo_prov
            },
            "metadata": {
                "title": metadata.get("title", ""),
                "venue": metadata.get("venue", ""),
                "publication_date": metadata.get("publication_date", ""),
                "publication_year": metadata.get("publication_year"),
                "publisher": metadata.get("publisher", ""),
                "issns": metadata.get("issns", []),
                "authors": metadata.get("authors", []),
                "crossref_raw": crossref_raw if crossref_raw else {},
                "datacite_raw": datacite_raw if datacite_raw else {}
            }
        }
        enriched_data_by_source[source].append(item)

        # Periodic save (every 50 records)
        if idx % 50 == 0:
            for s, items in enriched_data_by_source.items():
                out_file = os.path.join(HERE, f"enriched_{s.lower()}.json")
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(items, f, indent=4)
            logging.info(f"Enrichment progress saved (checkpoint) for {len(enriched_data_by_source)} datasets.")

    # Final save
    for s, items in enriched_data_by_source.items():
        out_file = os.path.join(HERE, f"enriched_{s.lower()}.json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=4)

    logging.info("=== Enrichment Statistics ===")
    total_processed = sum(len(items) for items in enriched_data_by_source.values())
    logging.info(f"Total processed publication pairs: {total_processed}")
    logging.info(f"PMCID-to-DOI conversions: {resolved_dois}")
    logging.info(f"Metadata records resolved: {resolved_metadata} ({resolved_metadata/total_processed*100:.2f}%)")
    logging.info(f"Enriched datasets successfully saved into {len(enriched_data_by_source)} JSON files.")


if __name__ == "__main__":
    main()
