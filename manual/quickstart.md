# Quickstart

From an empty machine to a parsed corpus in three commands, using nothing but the SEC's
public archive. All three respect the SEC's fair-access rules (a declared `User-Agent`, at
most ten requests per second, backoff on 429 and 503).

```
export EDGAR_ITEMIZE_DATA_ROOT=/data/edgar
export EDGAR_ITEMIZE_USER_AGENT="Jane Doe jane@example.org"
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
