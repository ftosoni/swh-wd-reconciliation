# Released corpus

The harvested `⟨DOI, repository⟩` corpus underlying the paper: **4,397** pairs
across four editorially-verified sources. One row per paper; authors are kept in
a separate table (one row per author) so the author pass can be restricted to
newly created articles.

```
import/JOSS.csv         3,545 papers
import/SoftwareX.csv      486 papers
import/IPOL.csv          275 papers
import/SIGMOD.csv         91 papers
import/authors/<venue>.csv   one row per author
```

These are the OpenRefine-ready flattenings produced by `build_venue_import.py`.
Downstream editorial refinements (URL-root normalisation, label cleanup,
duplicate handling, the read-only Wikidata precheck) are applied by
`prep_openrefine.py` and `precheck_wikidata.py` in `../scripts/`.

## Main table columns (`import/<venue>.csv`)

| Column | Wikidata property | Meaning |
|---|---|---|
| `article_doi`, `article_doi_url` | `P356` | article DOI (and its resolver URL) |
| `article_title` | `P1476` | article title |
| `software_label_from_repo`, `software_label_from_title` | — | candidate software labels |
| `repo_url_P1324` | `P1324` | source code repository URL |
| `swhid_P6138` | `P6138` | Software Heritage identifier (qualified-full) |
| `publication_date_P577` | `P577` | publication date |
| `publisher_P123` | `P123` | publisher |
| `language_P407` | `P407` | language |
| `venue_name_P1433`, `issn` | `P1433` | publication venue |
| `crossref_ref_url`, `crossref_stated_in` | reference | bibliographic provenance |
| `repo_ref_url_P854`, `repo_stated_in` | `P854` | repository-mapping provenance |
| `retrieved_P813` | `P813` | retrieval date |

## Authors table columns (`import/authors/<venue>.csv`)

| Column | Wikidata property | Meaning |
|---|---|---|
| `article_doi` | `P356` | joins back to the main table |
| `series_ordinal_P1545` | `P1545` | author order |
| `author_name_P2093` | `P2093` | author name string |

## Provenance

Every repository/SWHID statement carries a reference block (*stated in*,
*reference URL*, *retrieved*). Bibliographic metadata comes from Crossref, with
DataCite and the NCBI ID Converter as fallbacks; repository mappings are stated
against each venue's own API or landing page.
