# swh-wd-reconciliation

A reconciliation pipeline that links scholarly publications to their archived
source code across **Software Heritage** (SWH) and **Wikidata**.

The pipeline harvests `⟨DOI, repository⟩` pairs from venues where the
paper-to-code link is explicit and editorially verified, resolves each
repository to a content-addressed Software Heritage identifier (SWHID), checks
Wikidata (read-only) for pre-existing items, and emits reviewable
**QuickStatements** / OpenRefine batches that model each publication and each
software project as two linked Wikidata items.

This repository accompanies the paper *From Code Archival to Knowledge Graph:
Bridging Software Heritage, COAR Notify and Wikidata* and contains the primary
code and the harvested datasets needed to reproduce its results. It intentionally
contains **no manuscript and no bibliography**.

> **Wikidata is only ever read.** The pipeline issues read-only SPARQL queries
> and produces candidate statements for human review. It performs **no automated
> writes** to the live knowledge base; every generated batch is inspected before
> any upload.

## Sources

The released corpus of **4,397** `⟨DOI, repository⟩` pairs is built from four
editorially-verified sources, where an author-declared repository accompanies
each publication by editorial policy (so the link is correct by construction
rather than inferred from prose):

| Source | Access | Pairs |
|---|---|---|
| JOSS (Journal of Open Source Software) | JSON catalogue API | 3,545 |
| SoftwareX (Elsevier) | Crossref (ISSN 2352-7110) + `ElsevierSoftwareX` GitHub mirrors | 486 |
| IPOL (Image Processing On Line) | landing-page BibTeX (native SWHIDs) | 275 |
| SIGMOD ARI | reproducibility report PDFs (2020–2024) | 91 |
| **Unified** | de-duplicated union | **4,397** |

Notes:

- The code also retains a **JORS** (Journal of Open Research Software) harvesting
  path. JORS is **not** part of the released corpus, but the venue is left
  supported in the harvesting and schema-generation code for completeness.
- Text-mining **mention corpora** such as Softcite and SoMeSci are **out of
  scope** for this pipeline: this project targets sources whose paper-to-code
  link is editorially verified, not recovered from free text.

## Two-ontology model

Each software-citation link joins two distinct entities, modelled as two Wikidata
items reusing existing classes and properties, aligned with schema.org and
CodeMeta:

- **Article** (`Q13442814` / `Q18918145` / `Q23927052`): DOI (`P356`), title
  (`P1476`), published in (`P1433`), publication date (`P577`), author (`P50`),
  and *main subject* (`P921`) pointing to the software.
- **Software** (`Q7397`): source code repository (`P1324`), SWHID (`P6138`),
  optional deposit DOI (`P356`), and *described by source* (`P1343`) pointing back
  to the article.

The `schemas/` directory holds the concrete OpenRefine/Wikibase schema
specifications that realise this model per venue.

## Pipeline

| Stage | Script(s) | What it does |
|---|---|---|
| 1. Harvest venues | `harvest_venues.py`, `harvest_sigmod.py` | Collect `⟨DOI, repo⟩` pairs from JOSS, SoftwareX, IPOL (and JORS), and from the SIGMOD ARI reports. |
| 1b. SIGMOD QA | `extract_sigmod_text.py`, `validate_sigmod_llm.py` | Persist report text and cross-validate the regex-extracted repository against an LLM read of each report. |
| 2. Enrich | `enrich_publications.py` | Add bibliographic metadata (Crossref / DataCite / NCBI) and provenance. |
| 3. Archive | `extract_repos.py`, `archive_individual.py` | Extract unique code-host repositories and submit them to SWH *Save Code Now*. |
| 4. Resolve SWHIDs | `retrieve_swhids.py` | Resolve each archived origin to a qualified-full SWHID near the publication date. |
| 5. Precheck (read-only) | `precheck_wikidata.py` | Flag software/article nodes that already exist in Wikidata, so imports enrich rather than duplicate. |
| 6. Build import | `build_venue_import.py`, `prep_openrefine.py`, `build_author_import.py`, `gen_ipol_schemas.py`, `build_ipol_qs.py`, `build_ipol_authors_qs.py`, `build_sigmod_qs.py`, `build_sigmod_authors_qs.py` | Flatten enriched data into OpenRefine-ready CSVs and generate QuickStatements batches (articles, software, cross-links, authors). |

Each candidate statement carries a structured reference block (source, evidence
URL/DOI, retrieval date) and is emitted in QuickStatements v2 for human review.

## Repository layout

```
scripts/   primary pipeline code (one file per stage; see table above)
schemas/   OpenRefine/Wikibase schema specifications per venue (the ontology mapping)
data/      the released harvested corpus (see data/README.md)
config.json.template   copy to config.json and fill in your own tokens
requirements.txt       third-party dependencies
LICENSE                BSD 3-Clause
```

## Setup

```bash
python -m venv .venv && . .venv/bin/activate      # optional
pip install -r requirements.txt
cp config.json.template config.json               # then add your SWH / GitHub tokens
```

The scripts read and write intermediate files (large enriched JSON dumps, HTTP
caches, resolution logs) alongside a working data directory. Those heavy
intermediates and any API tokens are **not** distributed; the scripts are
provided for method transparency and to regenerate the released datasets from the
public APIs.

## License

BSD 3-Clause License. Copyright (c) 2026 Francesco Tosoni. See [LICENSE](LICENSE).
