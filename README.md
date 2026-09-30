# edgar-itemize

edgar-itemize is a deterministic, rule-based parser that recovers the agenda structure of
SEC EDGAR filings: Part, Item and sub-heading in Form 10-K and 10-Q bodies; Article,
Section and clause in credit agreements filed as EX-10 exhibits; the Item-like headings of
EX-13 annual reports. It works across the whole EDGAR era, from 1993 plain text through
publisher HTML to inline XBRL, and addresses every node by byte offset into the raw
full-submission `.txt` file, so its output can be shared and checked without redistributing
filings. Every node carries the ids of the rules that produced it, and every rejected
heading candidate is written out with its reason.

The manual, the long form of everything below, is at
<https://malcolmwardlaw.github.io/edgar-itemize/>.

## Install

From PyPI (Python 3.11 or later):

```
pip install edgar-itemize
```

As a container, the frozen reference artifact (digest-pinned base image, `uv` and every
dependency from the lockfile). Pull it by the digest given in the release notes of the
version you cite:

```
docker pull ghcr.io/malcolmwardlaw/edgar-itemize:<version>
docker run --rm -v /path/to/edgar:/data:ro ghcr.io/malcolmwardlaw/edgar-itemize@sha256:<digest> verify --data-root /data
```

From a clone, with [uv](https://docs.astral.sh/uv/) (prefix the commands below with
`uv run`):

```
git clone https://github.com/MalcolmWardlaw/edgar-itemize
cd edgar-itemize
uv sync --all-extras
uv run pytest -q
```

The parser needs only `regex`, `pyarrow` and `pyyaml`; HTML is read with a vendored copy of
the standard library's `html.parser`, which reports exact source offsets. The browser viewer
and the LLM judge are optional extras (`pip install 'edgar-itemize[viewer,judge]'`).

## From nothing to a parsed corpus

The parser reads raw full-submission files from a mirror laid out as the SEC serves them.
Three commands build that mirror from the SEC's public archive and parse it:

```
export EDGAR_ITEMIZE_DATA_ROOT=/data/edgar                     # the mirror root; no default
export EDGAR_ITEMIZE_USER_AGENT="Jane Doe jane@example.org"    # required by the SEC

# 1. a manifest from the public quarterly index (master.idx, cached under the data root)
edgar-itemize manifest --years 2019-2020 --no-only-present --out manifest_10k_2019_2020.parquet

# 2. the submission files it names (only those you do not already have)
edgar-itemize fetch --manifest manifest_10k_2019_2020.parquet --dry-run   # counts, no network
edgar-itemize fetch --manifest manifest_10k_2019_2020.parquet

# 3. parse
edgar-itemize parse --manifest manifest_10k_2019_2020.parquet --out run_10k --kind 10k \
    --workers 8 --no-text --partition-by year --progress
```

* `manifest` keeps the 10-K family by default (`--forms` for others; `10-Q-family` expands
  to the 10-Q forms), drops `/A` amendments unless `--include-amendments`, and takes
  *filing* years. `--no-only-present` is for a manifest that feeds `fetch`; by default only
  rows whose file is already in the mirror are kept.
* `fetch` skips files already present, writes each file atomically, and logs the SHA-256 of
  every file it saved to `$EDGAR_ITEMIZE_DATA_ROOT/fetch_log.jsonl`. Both commands follow the
  SEC's fair-access rules (a declared `User-Agent`, at most ten requests per second, backoff
  on HTTP 429 and 503). The full 10-K corpus is about 230,000 files and several hundred GB;
  if you already hold a mirror, skip `fetch`.
* `parse --kind` is `10k` (the primary document of a 10-K or 10-Q submission), `ex10`,
  `ex13` or `text` (a bare text file with no SGML envelope). The output does not depend on
  `--workers` or on partitioning, and a partitioned run is resumable.

Mirror layout:

```
$EDGAR_ITEMIZE_DATA_ROOT/
  archives/edgar/data/<CIK>/<accession>.txt     the full-submission files, read as latin-1
  full-index/<year>/QTR<n>/master.idx           the cached quarterly indexes (manifest)
  fetch_log.jsonl                               what fetch saved, with SHA-256
```

One filing at a time, with the scored candidates (`-c`) and the rejected ones with their
reasons (`-r`); a file path needs no data root:

```
edgar-itemize show /data/edgar/archives/edgar/data/320193/0000320193-20-000096.txt -c -r
```

## Output

A run writes three parquet tables per partition: `nodes` (one row per heading in the tree:
label, title, byte span, ordinal path, confidence, rule ids), `documents` (one row per
parsed document: era and publisher profile, counts, items found, the input file's SHA-256)
and `rejected` (every heading candidate that was dropped, with its reason). Files are read
as latin-1 so that one character is one byte; slicing the raw file at a node's
`raw_start:raw_end` gives exactly its span. Every column, every closed vocabulary and what
the version promises about each is in the
[output contract](https://github.com/MalcolmWardlaw/edgar-itemize/blob/main/docs/OUTPUT_CONTRACT.md).

## Reproducibility

> Two runs of the same release of edgar-itemize on input files with the same `input_sha256`
> produce the same `nodes` and `rejected` rows and the same `documents` rows, regardless of
> machine, worker count or partitioning.

Each release ships a conformance set of about 2,000 documents with their input and output
hashes (the inputs are never redistributed; the manifest resolves against your mirror).
Check an install with:

```
edgar-itemize verify --data-root /path/to/edgar
```

It prints one line with the input and output hash counts and exits 0 only when every
present input matches and every output hash equals the shipped one; quote that line in a
replication package. A tagged release is never changed; a wrong output is recorded in
[ERRATA.md](https://github.com/MalcolmWardlaw/edgar-itemize/blob/main/ERRATA.md) against the
version and fixed in the next minor. The policy is
[docs/VERSIONING.md](https://github.com/MalcolmWardlaw/edgar-itemize/blob/main/docs/VERSIONING.md).

## Measured accuracy

At the Turn 13 close, whose tree behaviour 1.0.0 carries, on the fixed 1,000-window audit
bank (`runs/judge/turn13-audit-rescore.txt`):

| measure | value |
|---|---|
| accepted precision | 94.4% |
| top-level precision | 98.2% |
| recall gap | 11.0% |

Agreement on 10-K Item boundaries with other open tools
(`runs/judge/turn13-external-before-after.txt`): datamule 91.6%; EDGAR-CORPUS 90.2%
(fiscal years 1993-2000) and 90.3% (2001-2020).

The `runs/...` names are the lab's identifiers for the stored artifacts the numbers were
read from; the full evaluation record behind them ships with release 1.0.1. How the
numbers were measured is in the manual's
[evaluation](https://malcolmwardlaw.github.io/edgar-itemize/evaluation/) page.

## Out of scope

* XBRL financial data and tables: the parser recovers headings, not figures.
* Text extraction beyond offsets: the output is spans into the raw file; slicing and
  cleaning the text is left to the user.
* Machine learning: none inside the parser, and no randomness.

## Viewer and judge

`edgar-itemize serve` (needs the `viewer` extra) shows a filing's text with the agenda
structure as colour bands, with the candidates and rejections; it reads its bank and run
registries from the directory named by `EDGAR_ITEMIZE_VIEWER_CONFIG`, and has none by default.
`edgar-itemize judge` (the `judge` extra) asks a language model about a filing's heading
candidates; it is evaluation tooling only and never part of the parser.

## Citing

Name the version you ran. The citation metadata is in
[CITATION.cff](https://github.com/MalcolmWardlaw/edgar-itemize/blob/main/CITATION.cff);
the Zenodo DOI is `<Zenodo concept DOI, added at the 1.0.0 release>`. See the manual's
[citing](https://malcolmwardlaw.github.io/edgar-itemize/citing/) page.

## License

MIT.

### Third-party code

`src/edgar_itemize/_html_parser.py` is a verbatim copy of CPython 3.11.15's
`Lib/html/parser.py` (`html.parser`), copyright the Python Software Foundation and
licensed under the Python Software Foundation License Version 2 (`LICENSES/PSF-2.0.txt`).
It is vendored so that the tokenisation of malformed HTML does not change with the
interpreter's patch level (`docs/VERSIONING.md` section 5).
