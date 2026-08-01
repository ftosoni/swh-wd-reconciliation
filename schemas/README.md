# Ontology schemas

OpenRefine / Wikibase schema specifications that realise the two-ontology model
(scholarly article + software instance) for each venue. They map the flat corpus
columns in `../data/import/` onto Wikidata statements, qualifiers, and reference
blocks.

Each venue is described by a small family of schemas:

- `*_schema_1_software.json` — the software item (`P31 = Q7397`, `P1324`, `P6138`, host qualifiers, references)
- `*_schema_1b_existing_swhid.json` — SWHID-only enrichment of pre-existing software items
- `*_schema_2_article.json` — the article item (`P356`, `P1476`, `P1433`, `P577`, ...)
- `*_schema_3a_article_p921.json` — the article-to-software cross-link (*main subject*, `P921`)
- `*_schema_3b_software_p1343.json` — the software-to-article back-link (*described by source*, `P1343`)
- `joss_schema_3_crosslinks.json` — combined cross-link schema (JOSS)

Venue coverage:

- `softwarex_wikibase_schema.json`, `wikidata_schema_spec.json` — SoftwareX and the shared property spec
- `joss_schema_*` — JOSS
- `ipol_schema_*` — IPOL (repo-less, SWHID-only: the software schema drops the `P1324` group)
- `jors_schema_*` — JORS (retained for completeness; not part of the released corpus)

The IPOL schemas are derived deterministically from the JORS schemas by
`../scripts/gen_ipol_schemas.py`.
