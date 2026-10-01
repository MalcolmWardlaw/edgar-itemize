# Quickstart

From an empty machine to a parsed corpus in three commands, using nothing but the SEC's
public archive. All three respect the SEC's fair-access rules (a declared `User-Agent`, at
most ten requests per second, backoff on 429 and 503).

```
export EDGAR_ITEMIZE_DATA_ROOT=/data/edgar
export EDGAR_ITEMIZE_USER_AGENT="Jane Doe jane@example.org"
```

## First: the starter set

Before building a manifest of your own, fetch and parse the nine filings of the
[live demo](demo-guide.md) and check that your install produces the release's output for
them. The manifests and hashes are under `examples/` in the repository (run from a clone):

```
for k in 10k ex10 ex13; do edgar-itemize fetch --manifest examples/sample_manifest_$k.parquet; done
for k in 10k ex10 ex13; do edgar-itemize parse --kind $k --manifest examples/sample_manifest_$k.parquet --out sample_run --no-text; done
python examples/check_hashes.py --run sample_run --expected examples/sample_hashes.parquet
edgar-itemize serve
```

The nine are three Apple 10-Ks (1994 to 1996), a 2006 financial-printer HTML 10-K, an
inline-XBRL 10-K (2024), a 2002 10-Q, two credit agreements filed as EX-10 exhibits and an
EX-13 annual report. They come in three manifests because each `--kind` selects its document
and grammar differently (the 10-Q parses with `--kind 10k`); all three parse into one run
directory. `fetch` downloads 9 files, 12,929,398 bytes (measured on the release machine's
mirror). `check_hashes.py` prints `9 in both ... output 9 equal` and `OK` when every
document's output hash equals the release's; it exits 1 otherwise. `serve` (it needs the
`viewer` extra) opens on <http://127.0.0.1:8765>: type an accession number in the box, e.g.
`0000320193-94-000016`, then Enter (the viewer finds the file under the data root), or a
submission's path relative to the data root, e.g. `archives/edgar/data/320193/0000320193-94-000016.txt`.
An exhibit opens by deep link with its sequence, e.g.
<http://127.0.0.1:8765/?acc=0000950144-97-003175&seq=4&corpus=ex10>. The viewer parses the
filing live, with the same parser.

### The covenant sample

`examples/sample_manifest_contracts.parquet` names ten EX-10 credit agreements (1995 to 2023,
plain text, early HTML, financial-printer HTML and recent filings; 10 files, 9,623,034 bytes,
two of them shared with the nine), each with an Affirmative Covenants and a Negative
Covenants Article or Section that has clauses beneath it; `examples/README.md` lists them.

```
edgar-itemize fetch --manifest examples/sample_manifest_contracts.parquet
edgar-itemize parse --kind ex10 --manifest examples/sample_manifest_contracts.parquet --out contracts_run --no-text
python examples/check_hashes.py --run contracts_run --expected examples/sample_hashes_contracts.parquet
```

The covenant Articles and Sections are found by title (their numbers vary), and their clauses
by span:

```python
import os
import polars as pl

key = ["accession_number", "sequence"]
nodes = pl.read_parquet("contracts_run/nodes-ex10.parquet")
paths = pl.read_parquet("examples/sample_manifest_contracts.parquet").select(*key, "archive_path")

cov = (nodes.filter(pl.col("level_kind").is_in(["article", "section"])
                    & pl.col("title").str.contains(r"(?i)(affirmative|negative)\s+covenant"))
            .join(paths, on=key))
clauses = (nodes.join(cov.select(*key, pl.col("title").alias("covenant"), pl.col("depth").alias("cov_depth"),
                                 pl.col("raw_start").alias("cov_start"), pl.col("raw_end").alias("cov_end")), on=key)
                .filter(pl.col("raw_start").is_between(pl.col("cov_start"), pl.col("cov_end"), closed="left")
                        & (pl.col("depth") > pl.col("cov_depth"))))
print(clauses.group_by(*key, "covenant").len().sort(key))

c = cov.row(0, named=True)   # the text of one covenant Article, as filed
with open(os.path.join(os.environ["EDGAR_ITEMIZE_DATA_ROOT"], c["archive_path"]), encoding="latin-1") as f:
    text = f.read()[c["raw_start"]:c["raw_end"]]
```

## 1. A manifest from the public index

```
edgar-itemize manifest --years 2019-2020 --no-only-present --out manifest_10k_2019_2020.parquet
```

`manifest` reads the SEC's quarterly `master.idx` files (about 25 MB each, downloaded once
and cached under the data root), keeps the 10-K family by default (`--forms` for others;
`10-Q-family` expands to the 10-Q forms), drops amendments unless `--include-amendments`,
and writes a parquet with the columns `accession_number, cik, submission_type, year,
agent_cik, archive_path`. `--years` is a range or a list of *filing* years. By default only
rows whose file already exists under the data root are kept; `--no-only-present` is what
you want when the manifest is for `fetch`.

A filing listed under several co-registrant CIKs yields one row, under the smallest CIK
whose file is present. The `cik` the parser stamps on its output is read from the file's
own header, not from the manifest, so this choice does not affect the output.

## 2. The raw files

```
edgar-itemize fetch --manifest manifest_10k_2019_2020.parquet --dry-run   # counts, no network
edgar-itemize fetch --manifest manifest_10k_2019_2020.parquet
```

`fetch` downloads each full-submission `.txt` into the mirror layout, skips files already
present, writes to a temporary name and renames into place, and appends the SHA-256, size
and time of every file it saved to `fetch_log.jsonl`. It is resumable. The full 10-K corpus
is about 230,000 files and several hundred gigabytes; at the SEC's rate that is most of a
day, so a researcher who already holds a mirror should skip this step.

## 3. Parse

```
edgar-itemize parse --manifest manifest_10k_2019_2020.parquet --out run_10k --kind 10k \
    --workers 8 --no-text --partition-by year --progress
```

* `--kind` selects the document and the grammar: `10k` (the primary document of a 10-K or
  10-Q submission; 10-Q submissions get the 10-Q grammar), `ex10` (an exhibit by sequence,
  contract grammar), `ex13` (an annual-report exhibit), `text` (a bare text file with no
  SGML envelope, contract grammar).
* `--no-text` leaves the normalised text out of the `documents` table. Recommended for any
  run you keep: the tables are then offsets and labels only.
* `--partition-by year` writes one set of parquet files per year and makes the run
  resumable: a partition whose files exist is skipped.
* `--workers N` parses in N processes. The output does not depend on N or on partitioning;
  a release asserts this on a full year of 10-Ks before it is tagged.

The run directory holds `nodes-10k-<year>.parquet`, `documents-10k-<year>.parquet` and
`rejected-10k-<year>.parquet`. An exception in one document becomes a `documents` row with
`error` set and no node rows; the run continues.

## 4. Look at the result

With `polars` (or `pandas` and `pyarrow`, the tables are plain parquet):

```python
import polars as pl

nodes = pl.read_parquet("run_10k/nodes-10k-2020.parquet")
docs = pl.read_parquet("run_10k/documents-10k-2020.parquet")

# Item 7 of one filing: its byte span in the raw submission file
item7 = nodes.filter((pl.col("accession_number") == "0000320193-20-000096") & (pl.col("label_canon") == "ITEM 7"))
print(item7.select("title", "raw_start", "raw_end", "path_str", "confidence", "rule_ids"))

# the text of that span, sliced straight out of the file
row = item7.row(0, named=True)
path = f"/data/edgar/archives/edgar/data/{row['cik']}/{row['accession_number']}.txt"
with open(path, encoding="latin-1") as f:
    text = f.read()[row["raw_start"]:row["raw_end"]]

# which Items each filing has, and how many were rejected as contents rows
print(docs.select("accession_number", "profile_era", "items_found", "n_nodes"))
```

The file is decoded as latin-1 so that one character is one byte; the slice above is
exactly the node's span, tags and all. What every column means, which vocabularies are
closed, and which columns the version promise covers is the [output contract](output-contract.md).

## 5. One filing at a time

```
edgar-itemize show /data/edgar/archives/edgar/data/320193/0000320193-20-000096.txt -c -r
```

prints the tree of one filing, and with `-c` and `-r` the scored candidates and the rejected
ones with their reasons. This is the fastest way to answer "why is Item 1A missing here":
usually the rejected table shows the heading and the reason it lost.

## 6. The viewer

```
pip install 'edgar-itemize[viewer]'
edgar-itemize serve            # http://127.0.0.1:8765
```

shows a filing's text with the agenda structure as colour bands, the candidates and the
rejections, for a run directory or a live parse. It reads its bank and run registries from
the directory named by `EDGAR_ITEMIZE_VIEWER_CONFIG`, and has none by default.

## 7. Check your install against the release

```
edgar-itemize verify
```

parses the release's conformance set (about 2,000 documents drawn from the four corpora)
from your mirror and compares every output hash with the shipped expectation. Run it once
after installing and quote its first line in a replication package
([reproducibility](reproducibility.md)).

## 8. The sections you want, in one parquet

The tables hold offsets, not text. `examples/pull_items.py` (in the repository) joins them
back to the raw files and writes one parquet with the text of every section you name:

```
python examples/pull_items.py --run run_10k --items "ITEM 1A,ITEM 7" --out items.parquet
```

One row per filing and Item, with `accession_number, cik, sequence, doc_type, label_canon,
title, raw_start, raw_end, text`; `text` is the Item's span as filed, HTML tags and all. A
raw file missing from the mirror is counted and skipped. `--items` takes any `label_canon`
values (`ITEM 1A`; on a 10-Q `ITEM I.2`; on a contract `ARTICLE 5`); `--limit 100` is a
quick look before the full pull.

In a credit agreement the covenants are an Article whose number varies by agreement, so
select it by title instead. For a run of EX-10 exhibits (`--kind ex10`, a manifest with a
`sequence` column):

```python
import polars as pl

nodes = pl.read_parquet("run_ex10/nodes-*.parquet")
docs = pl.read_parquet("run_ex10/documents-*.parquet").select("accession_number", "sequence", "manifest_cik")

cov = (nodes.filter((pl.col("level_kind") == "article")
                    & pl.col("title").str.to_lowercase().str.contains("covenant"))
            .join(docs, on=["accession_number", "sequence"]))

def span(row):
    path = f"/data/edgar/archives/edgar/data/{row['manifest_cik']}/{row['accession_number']}.txt"
    with open(path, encoding="latin-1") as f:
        return f.read()[row["raw_start"]:row["raw_end"]]

cov = cov.with_columns(pl.Series("text", [span(r) for r in cov.iter_rows(named=True)]))
cov.select("accession_number", "sequence", "label_canon", "title", "raw_start", "raw_end", "text") \
   .write_parquet("covenants.parquet")
```

An Article's span runs to the next Article, so its text includes every Section and clause
beneath it; the clauses are their own rows in `nodes` (`level_kind` `section`,
`clause_alpha`, ...) when a study needs them one by one.
