# edgar-itemize versioning policy

edgar-itemize is released as tagged versions that are frozen and deterministic, so that a
paper can say "we pulled Items 1, 7 and 8 with edgar-itemize vX.Y.Z" and anyone who runs
that version on the same filings gets the same rows. This document is the policy; the
output it applies to is `docs/OUTPUT_CONTRACT.md`. The rationale and the release phases are
in `docs/RELEASE_PLAN.md` (sections 1 and 3).

A `runs/...` name beside a number is the lab's identifier for the stored artifact the number
was read from; the artifacts are not in this repository, and the curated evaluation record
that resolves them ships with release 1.0.1 (`docs/RELEASE_PLAN.md` decision D3).

## 1. One version

The package version in `pyproject.toml` is the only version. It is read through
`importlib.metadata` at import time, and it is what every output row carries in
`parser_version` and `normalizer_version` (`documents` and `nodes`). The two columns are
kept for schema stability and always hold the same string. (Before 1.0.0 the parser
stamped hand-maintained constants, `0.4.0` and `0.2.0`, that were last bumped at Turn 5;
every run from `full_v25` to `full_v30` carries them despite eight turns of tree changes.
From 1.0.0 the stamp is the code.)

Versions are `MAJOR.MINOR.PATCH`. Pre-release tags (`1.0.0rc1`) are development builds
and promise nothing.

## 2. What each part of the version means

**Major.** An output schema change, or a change to label canonicalisation, path semantics
or span rules: anything that changes what `ITEM 7` or a `raw_start` means, what a column
is called or typed, or a closed vocabulary (`level_kind`, `label_canon`, `meta`, `segment`,
`rejected.reason`, `profile_era`, ...; the list is `OUTPUT_CONTRACT.md` section 9). A major
version announces itself one minor ahead in `CHANGELOG.md`.

**Minor.** Any tree-behaviour change: a rule added, amended or retired, so that some node
is added, removed, moved or re-parented, or some candidate's rejection reason changes. Every
turn close that merged a build is a minor release. A minor version may append a column at
the end of a table and may add or retire `rule_ids`; it changes nothing else in the schema
or the closed vocabularies. Its changelog entry names the gate (`scripts/run_diff.py`
before/after on every corpus) that measured the change.

**Patch.** Output-identical changes only: performance, the normalisation cache, packaging,
documentation, the CLI, the viewer. Proven, not assumed: a patch is tagged only when a
`run_diff` of a re-parse against the previous tag is an identity on `nodes` and `rejected`
and on every `documents` column but the version stamp, on all five corpora of record
(10-K, 10-Q, EX-10, EX-13, and the private contracts-text corpus).

## 3. Frozen means frozen

* A tagged release is never modified, re-tagged or deleted. The tag, the PyPI distribution,
  the container image and the Zenodo record for a version are permanent.
* Nothing is yanked from PyPI.
* A wrong output in a released version is recorded in `ERRATA.md` against that version,
  with the accession list, and fixed in the next minor version. The released version keeps
  producing the wrong output, by design: that is what makes it citable.
* The evaluation tooling (the viewer, the judge client, `scripts/`) is not covered by the
  promise and may change in any version.

## 4. The reproducibility promise

The unit of reproducibility is the raw full-submission `.txt` file, not the accession
number: the SEC does not rewrite filings, but it occasionally removes one and local mirrors
differ. Every `documents` row therefore carries `input_sha256` (SHA-256 of the file's bytes)
and `input_bytes`, and the promise is stated over inputs with matching hashes:

> Two runs of the same release of edgar-itemize on input files with the same `input_sha256`
> produce the same `nodes` and `rejected` rows and the same `documents` rows, regardless of
> machine, worker count or partitioning.

From 1.0.0 the `cik` column is itself derived from the input (the SGML header's first FILER
`CENTRAL INDEX KEY`, decision D9 in `docs/RELEASE_PLAN.md`), so the promise covers it: the
same bytes give the same `cik` whichever CIK directory of a mirror they were read from.
Only `documents.manifest_cik` and `filed_year` come from the manifest.

The parser holds no randomness and no learned component (project rule 1: nothing from a
language model enters the parser). Worker count and `--partition-by` invariance are asserted
by a test in release 1.0.0 (`docs/RELEASE_PLAN.md` section 4 item 3), not assumed.

## 5. The environment is part of the version

The HTML normaliser tokenises with **a vendored copy of CPython 3.11.15's `html.parser`**
(`src/edgar_itemize/_html_parser.py`, PSF-2.0, `LICENSES/PSF-2.0.txt`), not the running
interpreter's. It was vendored in R1 after the Python matrix showed the stock parser drifting
between patch releases on tag-soup filings: CPython 3.12.14 (the container) and 3.13.12 /
3.14.3 each swallowed the rest of a document after a numeric character reference split by a
line wrap (`&#` + newline + `160;`) or run into a hex-letter word (`&#147Change`), so 3 and 1
of the 2,001 conformance documents hashed differently (`runs/r1_docker_verify.txt`,
`runs/r1_py3.13_verify.json`, constructs in `runs/r1e_snippets.txt`). Every baseline and gate
of record was produced by the 3.11.15 parser, so that is the behaviour of record and the
vendored copy fixes it independently of the interpreter (`tests/test_html_parser_vendored.py`
records its event stream; `runs/r1e_matrix.txt` is the gate). The parser's own dependencies,
`html.unescape`, the `html.entities` tables and `_markupbase`, were checked across 3.11.15,
3.12.13, 3.12.14, 3.13.12 and 3.14.3 (`runs/r1e_stdlib_diff.txt`: identical, or differing in
comments only) and are still imported from the standard library. Label matching uses the
third-party `regex` module, which can also change behaviour between package releases. So a
release pins its dependencies (`uv.lock`) and states:

* the **verified Python versions**: the CPython versions under which the release's
  conformance set was parsed and produced identical output hashes (measured on 3.11, 3.12,
  3.13 and 3.14 on the host and on the container's interpreter; the release notes list which
  of them agree, from the matrix artifact of the release's R1 gate);
* a **container image by digest** (`ghcr.io/malcolmwardlaw/edgar-itemize:<version>`), built
  from a pinned `python:3.x-slim` digest with `uv sync --frozen`. The image is the artifact
  that is actually frozen; `pip install edgar-itemize==<version>` on a verified Python is the
  convenient approximation.

If a Python version ever produces different output, the release notes say so and the
container image is the reference.

## 6. How to check an install

Each release ships a conformance set under `conformance/<version>/`:

* `manifest.parquet`: `accession_number, cik, sequence, kind, year, archive_path,
  submission_type, profile_era` for about 2,000 documents drawn deterministically from the
  four EDGAR baselines (800 10-K, 600 10-Q, 400 EX-10, 200 EX-13; strata `(profile_era,
  year)`, largest-remainder allocation, keyed SHA-256 bottom-k inside each stratum with the
  fixed seed `edgar-itemize-conformance-1`, no RNG) plus every `tests/data/` fixture as kind
  `fixtures`. `archive_path` is relative to the data root
  (`archives/edgar/data/<cik>/<accession>.txt`), so the manifest resolves on any mirror; the
  inputs are never redistributed. The set draws from 10-K, 10-Q, EX-10 and EX-13 plus the
  test fixtures (`conformance/1.0.0/README.md`); the private contracts-text corpus is not
  in it, so the deterministic claim for `--kind text` rests on the fixtures and the tests,
  not on a drawn set.
* `expected.parquet`: per document, `input_sha256` and `input_bytes` of the raw file and
  `output_sha256`, the canonical output hash below, computed by parsing every document fresh
  with the release, plus `n_nodes` and `n_rejected`.
* `README.md`: the draw parameters and the stratum table.

```
edgar-itemize verify [--data-root DIR] [--set conformance/<version>] [--workers N] [--json]
```

parses the set offline, checks every input hash first and reports `input differs` and
`input missing` separately from `output differs`, then compares the output hashes, and
prints one line:

```
verify 1.0.0: 2001 documents, 2001 input ok, 0 input differs, 0 input missing, 2001 output ok, 0 output differs
```

followed by the failing documents (50 unless `--json`). The exit code is 0 only when every
input that is present matches and every output matches; a missing input (a filing the
mirror lacks) is reported but does not fail the check, because the promise is stated over
inputs with matching hashes. This is the command a replication package tells its reader to
run.

### The canonical output hash

`edgar_itemize.conformance.document_hash(nodes_rows, rejected_rows)` is the SHA-256 of the
following byte string, built from one document's rows of the `nodes` and `rejected` tables
(`docs/OUTPUT_CONTRACT.md`):

1. every row is first passed through the output schema (`schema.NODE_SCHEMA`,
   `schema.REJECTED_SCHEMA`), so that a value has the column's type whether the row came
   from the parser or from a parquet file: `confidence` and `score` are float32, `sequence`
   int32, `path` a list of int16, and so on;
2. the columns `parser_version` and `normalizer_version` are dropped; nothing else is. The
   hash is therefore the same across a patch release, which is how a patch release is shown
   to be output-identical;
3. each row is serialised as one line of JSON with keys sorted, `ensure_ascii=True`,
   `separators=(",", ":")`, `None` as `null`, booleans as `true`/`false`, lists in their
   stored order, integers as decimal, and floats printed by Python `repr` of the double
   that holds the float32 value (what `json.dumps` emits; `0.9` stored as float32 prints as
   `0.8999999761581421`);
4. the byte string is the ASCII line `nodes`, then the node rows sorted by `node_id`, then
   the ASCII line `rejected`, then the rejected rows sorted by `(block_idx, raw_start, kind,
   label_canon, reason, serialised row)`, every line terminated by `\n`.

A document with no rows hashes to `sha256("nodes\nrejected\n")`. The same function, applied
to a run directory (`edgar-itemize conformance hashes --run DIR --kind K --out FILE.parquet`,
`conformance.manifest_hashes`), produces the per-document hash manifest of a full baseline
(`accession_number, sequence, input_sha256, input_bytes, output_sha256, n_nodes,
n_rejected`; `output_sha256` null on error rows, the input columns null for runs that
predate 1.0.0). One such parquet per corpus is published with each release as a release
asset and on the Zenodo record, so a researcher's own full run can be compared document by
document.

## 7. Release cadence

Each turn close is a minor release: bump the version, write the `CHANGELOG.md` entry from
the turn report, re-parse the five corpora as the release's reference runs, publish the
hashes and the dataset, tag. The project's existing cadence is the release cadence.
