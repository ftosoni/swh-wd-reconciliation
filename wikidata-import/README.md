# Wikidata import batches

Provenance record of how the released corpus was imported into Wikidata. The
pipeline in `scripts/` only generates candidate batches; every batch listed here
was inspected and then run manually by the author under the Wikidata account
`Francesco Tosoni (SSSA)`.

## `batches.csv`

One row per upload batch attributable to this project (SoftwareX, JOSS, IPOL,
SIGMOD), in chronological order, as recorded by
[EditGroups](https://editgroups.toolforge.org/):

| Column | Meaning |
|---|---|
| `venue` | source venue of the imported rows |
| `tool` | `OpenRefine` (Wikibase uploader) or `QuickStatements 3.0` |
| `batch_id` | OpenRefine edit-group id or QuickStatements batch number |
| `started_utc`, `ended_utc` | batch time span |
| `edits` | page revisions counted by EditGroups (one revision may carry several statements) |
| `new_items` | items created by the batch |
| `summary` | edit summary entered when running the batch |
| `editgroups_url` | per-batch page listing every edit, with diffs and revert status |
| `run_report` | QuickStatements run report in `qs-reports/`, when released |

Imports follow the two-pass scheme described in the paper: a first pass creates
the missing software and article items, and a second pass, fed with the QIDs of
the first pass, adds the `P921` / `P1343` cross-links and the author strings
(`P2093` + `P1545`). The two late SoftwareX batches are a clean-up of DOI (`P356`)
statements: removal of redundant lower-case duplicates (44920) and capitalisation
on the newly created articles (44921).

## `qs-reports/`

QuickStatements 3.0 run reports (`batch-NNNNN-report.csv`), one line per command:
`batch_id, index, operation, status, error, message, entity_id, raw_input`.
`entity_id` is the item touched (or created) by each command, so the reports map
every generated statement to its live Wikidata item.

Note on batch 44740 (IPOL articles): its 153 `Error` rows stem from QuickStatements'
`LAST` placeholder. 152 are software-to-article `P1343` statements that used `LAST`
as a *value*, which the backend does not support; one is the second `P921` of the
two-software article `10.5201/ipol.2011.cm_fds`, where `LAST` had lost its context.
All 153 were re-issued with explicit QIDs in batch 44742, which completed with no
errors.
