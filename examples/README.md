# examples/

Worked examples and a starter set. The scripts need only the installed package (they read
the run with `pyarrow`); the parquets are small enough to keep in git. The filings themselves
are never shipped: the manifests name them, `edgar-itemize fetch` downloads them from the
SEC, and the hash parquets say what a parse of them must produce.

## Scripts

| file | what it does |
|---|---|
| `pull_items.py` | joins a run's `nodes` table back to the raw files and writes one parquet with the text of every section you name (`--items "ITEM 1A,ITEM 7"`) |
| `coverage.py` | how often the core Items were found in a run, per year, era or publisher |
| `check_hashes.py` | compares a run you parsed with a release's per-document hash parquet; exit 0 when every document with the same input hash has the same output hash |

Each script's docstring is its reference (`python examples/<name>.py --help`).

## The starter set

| file | rows | what |
|---|---|---|
| `sample_manifest_10k.parquet` | 6 | the demo's five 10-Ks and one 10-Q, parsed with `--kind 10k` |
| `sample_manifest_ex10.parquet` | 2 | the demo's two credit agreements (EX-10, by `sequence`), `--kind ex10` |
| `sample_manifest_ex13.parquet` | 1 | the demo's EX-13 annual report, `--kind ex13` |
| `sample_hashes.parquet` | 9 | per-document `input_sha256` and `output_sha256` of the nine |
| `sample_manifest_contracts.parquet` | 10 | ten credit agreements for the covenant use case, `--kind ex10` |
| `sample_hashes_contracts.parquet` | 10 | their hashes |

The nine filings are the ones in the [live demo](https://malcolmwardlaw.github.io/edgar-itemize/demo/).
The manifests carry `accession_number, cik, sequence, kind, year, submission_type,
agent_cik, archive_path`, with `archive_path` relative to the data root
(`archives/edgar/data/<cik>/<accession>.txt`), so they resolve on any mirror. `kind` is the
corpus (`10k`, `10q`, `ex10`, `ex13`); the 10-K and 10-Q rows share one manifest because both
parse with `--kind 10k`. The hash parquets have the columns `edgar-itemize conformance hashes`
writes; both were computed from a fresh parse of these manifests and equal the release's
reference runs row for row (9 of 9 and 10 of 10).

Size, measured on the file sizes of the submissions on the release machine's mirror: the
nine are 12,929,398 bytes, the ten agreements 9,623,034 bytes; two agreements are in both,
so the whole set is 17 files and 21,185,895 bytes.

```
export EDGAR_ITEMIZE_DATA_ROOT=/data/edgar
export EDGAR_ITEMIZE_USER_AGENT="Jane Doe jane@example.org"
for k in 10k ex10 ex13; do edgar-itemize fetch --manifest examples/sample_manifest_$k.parquet; done
for k in 10k ex10 ex13; do edgar-itemize parse --kind $k --manifest examples/sample_manifest_$k.parquet --out sample_run --no-text; done
python examples/check_hashes.py --run sample_run --expected examples/sample_hashes.parquet
```

### The ten agreements

Drawn from the 1.0.0 reference EX-10 run by `scripts/sample_manifests.py`: the two demo
agreements, then one per era bucket, each with exactly one Article or Section titled
Affirmative Covenants and one titled Negative Covenants, each of those with at least 10
nodes beneath it, at least 250 clause nodes, a tree depth of at least 5, a submission file
of at most 2 MB, and a filer not already chosen; inside a bucket the candidate with the
smallest keyed hash (`conformance.draw_key`) is taken. The filer is the submission header's
conformed name; node counts are the reference run's; "nodes beneath" counts the tree
descendants of the covenant node; bytes is the size of the submission file.

| accession, seq | filer | year | era (publisher) | nodes | clauses | depth | covenant headings (nodes beneath) | bytes | bucket |
|---|---|---|---|---|---|---|---|---|---|
| 0000950144-97-003175, 4 | AGCO CORP /DE | 1997 | text (bowne) | 759 | 696 | 8 | SECTION 5.01 Affirmative Covenants (16); SECTION 5.02 Negative Covenants (113) | 910,458 | demo |
| 0001193125-08-240082, 2 | BOEING CO | 2008 | html_publisher (donnelley) | 352 | 282 | 6 | SECTION 4.01 Affirmative Covenants of TBC (13); SECTION 4.02 General Negative Covenants of TBC (16) | 456,079 | demo |
| 0000950129-95-000998, 2 | GLOBAL NATURAL RESOURCES INC /NJ/ | 1995 | text (bowne) | 469 | 380 | 8 | SECTION 6.01 Affirmative Covenants (50); SECTION 6.02 Negative Covenants (53) | 264,783 | text, mid-1990s |
| 0000950134-02-009531, 3 | TRINITY INDUSTRIES INC | 2002 | text (bowne) | 565 | 360 | 5 | ARTICLE 6 AFFIRMATIVE COVENANTS (25); ARTICLE 7 NEGATIVE COVENANTS (54) | 997,398 | text, 2000s |
| 0000914760-07-000098, 3 | PATRICK INDUSTRIES INC | 2007 | html_early (other) | 355 | 258 | 6 | ARTICLE 5 AFFIRMATIVE COVENANTS (25); ARTICLE 6 NEGATIVE COVENANTS (66) | 941,931 | early HTML, 2000s |
| 0001364100-11-000014, 2 | Cal Dive International, Inc. | 2011 | html_early (self) | 547 | 415 | 8 | ARTICLE 6 AFFIRMATIVE COVENANTS (37); ARTICLE 7 NEGATIVE COVENANTS (93) | 1,437,884 | early HTML, 2010s |
| 0000950123-05-007366, 2 | LIFEPOINT HOSPITALS, INC. | 2005 | html_publisher (bowne) | 415 | 294 | 7 | ARTICLE 5 AFFIRMATIVE COVENANTS (32); ARTICLE 6 NEGATIVE COVENANTS (99) | 455,468 | publisher HTML, 2004-2007 |
| 0001193125-12-516314, 2 | Oaktree Capital Group, LLC | 2012 | html_publisher (donnelley) | 364 | 260 | 6 | ARTICLE 5 AFFIRMATIVE COVENANTS (21); ARTICLE 6 NEGATIVE COVENANTS (70) | 603,026 | publisher HTML, 2010-2014 |
| 0001213900-23-070954, 2 | B. Riley Financial, Inc. | 2023 | html_publisher (edgar_agents) | 1015 | 866 | 8 | ARTICLE 5 AFFIRMATIVE COVENANTS (73); ARTICLE 6 NEGATIVE COVENANTS (111) | 1,772,527 | recent publisher HTML, 2019- |
| 0001654954-23-008179, 2 | EASTERN CO | 2023 | html_generic (other) | 651 | 485 | 6 | ARTICLE 6 AFFIRMATIVE COVENANTS (56); ARTICLE 7 NEGATIVE COVENANTS (67) | 1,783,480 | recent generic HTML, 2019- |

### Rebuilding the set

`scripts/sample_manifests.py` (in the repository) rebuilds it at a release: `manifests`
writes the four manifests from `manual/demo/bake.json` and the reference runs, and `hashes`
writes a hash parquet from a fresh parse of them and cross-checks it against the reference
runs. Its docstring has the commands.
