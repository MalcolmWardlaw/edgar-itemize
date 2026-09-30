# Command line

`edgar-itemize <command> --help` prints the options of any command. The commands a user
of the parser needs are `manifest`, `fetch`, `parse`, `show` and `verify`; the rest are
evaluation and maintenance tooling.

## `manifest`

Build a parse manifest from the SEC's public quarterly index.

```
edgar-itemize manifest [--forms FORMS] [--years YEARS] [--data-root DIR] [--index-dir DIR]
                       --out FILE.parquet [--include-amendments] [--only-present | --no-only-present]
                       [--user-agent UA] [--refresh]
```

| option | meaning |
|---|---|
| `--forms` | comma list of form types; default the 10-K family `10-K,10-K405,10-KT,10-KSB,10-KSB40`; `10-Q-family` expands to `10-Q,10QSB,10-QSB,10-QT` |
| `--years` | a range `2019-2020` or a list `2019,2021`, of *filing* years |
| `--data-root` | the mirror root (default `$EDGAR_ITEMIZE_DATA_ROOT`) |
| `--index-dir` | where `master.idx` files are cached (default `<data-root>/full-index`) |
| `--include-amendments` | keep the `/A` forms |
| `--only-present` / `--no-only-present` | keep only rows whose file exists under the data root (default on); use `--no-only-present` before `fetch` |
| `--user-agent` | `"<name> <email>"`, required by the SEC (default `$EDGAR_ITEMIZE_USER_AGENT`) |
| `--refresh` | re-download the index files even if cached |

Output columns: `accession_number, cik, submission_type, year, agent_cik, archive_path`.

## `fetch`

Download the full-submission files a manifest names into the mirror layout.

```
edgar-itemize fetch --manifest MANIFEST [--data-root DIR] [--user-agent UA] [--limit N] [--dry-run] [--progress]
```

Skips files already present, writes atomically, logs every saved file's SHA-256 to
`<data-root>/fetch_log.jsonl`, retries with backoff on HTTP 429 and 503, and never exceeds
ten requests per second. `--dry-run` counts present and missing files without the network.

## `parse`

Parse every document a manifest names.

```
edgar-itemize parse --manifest MANIFEST --out DIR [--kind {10k,ex10,ex13,text}] [--limit N]
                    [--no-text] [--progress] [--workers N] [--partition-by COLUMN] [--overwrite]
                    [--norm-cache DIR] [--norm-cache-mode {off,read,write,readwrite}]
```

| option | meaning |
|---|---|
| `--kind` | `10k` (default): the primary document of a 10-K or 10-Q submission; `ex10`: the exhibit at the manifest row's `sequence`, contract grammar; `ex13`: an annual-report exhibit, 10-K grammar with a heading pass; `text`: a bare text file, contract grammar |
| `--no-text` | do not keep the normalised text in `documents` (recommended) |
| `--workers` | processes; the output is the same for any value |
| `--partition-by` | a manifest column (usually `year`); one set of files per value; a partition whose files exist is skipped, so a run is resumable |
| `--overwrite` | re-parse partitions that exist |
| `--norm-cache`, `--norm-cache-mode` | a per-document cache of the normalisation prefix; `off` by default. Output is identical with or without it |

Writes `nodes-<kind>-<partition>.parquet`, `documents-<kind>-<partition>.parquet`,
`rejected-<kind>-<partition>.parquet` (no partition suffix without `--partition-by`), and a
`normcache-<partition>.parquet` sidecar when the cache is on.

## `show`

Parse one filing and print its tree.

```
edgar-itemize show PATH_OR_ACCESSION [--manifest M] [--cik CIK] [--type TYPE] [--sequence N] [-c] [-r]
```

`PATH_OR_ACCESSION` is a submission file, or an accession number looked up in `--manifest`.
`--sequence` picks a specific `<DOCUMENT>` block; `-c` prints the scored candidates and
`-r` the rejected ones with their reasons.

## `verify`

Re-parse the release's conformance set and compare input and output hashes, offline.

```
edgar-itemize verify [--data-root DIR] [--set DIR] [--repo-root DIR] [--workers N] [--json] [--progress]
```

Prints one line, `verify <version>: N documents, N input ok, N input differs, N input missing,
N output ok, N output differs`, then the failing documents (50 unless `--json`). Exit 0 only
when every present input matches and every output matches
([reproducibility](reproducibility.md)).

## `conformance`

Maintainer commands behind `verify`.

```
edgar-itemize conformance draw  ...          # draw the set for a release, parse it fresh, write manifest/expected/README
edgar-itemize conformance hashes --run DIR --kind K --out FILE.parquet   # per-document hash manifest of a run
```

The hash manifest (`accession_number, sequence, input_sha256, input_bytes, output_sha256,
n_nodes, n_rejected`) is published per corpus with each release, so a full run of your own
can be compared with the reference run document by document.

## `report`

Gold-free sanity metrics for a run directory, grouped by `--by` columns (for instance
`profile_era,profile_publisher`), with the `--worst N` groups listed.

## `serve`

The browser viewer (`[viewer]` extra): `edgar-itemize serve [--host H] [--port P]`, on
`127.0.0.1:8765` by default. It reads its bank and run registries from the directory named
by `EDGAR_ITEMIZE_VIEWER_CONFIG`, and has none by default.

## `gold-init`, `eval`, `judge`

Evaluation tooling: write hand-correctable label skeletons from a run, score a run against
reviewed labels, and ask a local language model about the heading candidates of one filing
(`[judge]` extra). Nothing from the judge enters the parser; it exists to measure the
parser, and it is not covered by the version promise.

## Environment variables

| variable | meaning |
|---|---|
| `EDGAR_ITEMIZE_DATA_ROOT` | the mirror root holding `archives/edgar/data/` |
| `EDGAR_ITEMIZE_USER_AGENT` | `"<name> <email>"` for SEC downloads |
| `EDGAR_ITEMIZE_VIEWER_CONFIG` | the directory holding the viewer's bank and run registries (none by default) |
| `EDGAR_ITEMIZE_OLLAMA` | the judge's model server (evaluation tooling only) |
