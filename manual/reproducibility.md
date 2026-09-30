# Reproducibility and versions

The point of a frozen release is that a sentence like *"we pulled Items 1, 7 and 8 with
edgar-itemize v1.0.0"* is checkable by anyone, years later, on any machine. This page says
what that promise covers and how to check it; the policy it summarises is the versioning
policy reproduced below.

## The promise in one sentence

> Two runs of the same release of edgar-itemize on input files with the same `input_sha256`
> produce the same `nodes` and `rejected` rows and the same `documents` rows, regardless of
> machine, worker count or partitioning.

The unit is the raw file, not the accession number: the SEC does not rewrite filings, but
it occasionally removes one and local mirrors differ. Every `documents` row carries the
SHA-256 and size of the file it was read from. Even the `cik` column is derived from the
file's own header, so the same bytes give the same rows whichever directory of a mirror
they were read from.

## How to check an install

```
edgar-itemize verify --data-root /path/to/edgar
```

Each release ships a conformance set: about 2,000 documents drawn deterministically from the
10-K, 10-Q, EX-10 and EX-13 corpora (stratified by era and year, keyed-hash sampling, no
random number generator), with each document's input hash and canonical output hash. The
inputs are never redistributed; the manifest resolves against your own mirror. `verify`
parses the set, reports inputs that differ or are missing separately from outputs that
differ, and exits 0 only when every present input and every output matches. Quote its first
line in a replication package.

The set draws from 10-K, 10-Q, EX-10 and EX-13 plus the test fixtures
(`conformance/1.0.0/README.md`), so the deterministic claim for `--kind text` (a bare
text file with no SGML envelope) rests on the fixtures and the tests, not on a drawn set.

For a full run of your own, each release also publishes a per-document hash manifest of its
reference run for every corpus, so your `nodes` and `rejected` rows can be compared with the
reference document by document (`edgar-itemize conformance hashes`).

## What the version number means

* **Patch** (1.0.x): output-identical. Packaging, performance, documentation, the viewer.
  Proven by a full re-parse against the previous tag before it is tagged.
* **Minor** (1.x.0): a tree-behaviour change: a rule added, amended or retired, so that
  some node is added, removed or moved. May append a column and add or retire rule ids;
  changes nothing else. The changelog names the gate that measured the change.
* **Major**: a schema change or a change to what a label, offset or path means.

A tagged release is never modified, re-tagged, deleted or yanked. A wrong output in a
released version is recorded in [errata](errata.md) against that version and fixed in the
next minor; the released version keeps producing the wrong output, by design, because that
is what makes it citable.

## The environment is part of the version

The HTML tokeniser is vendored from CPython 3.11.15 rather than taken from the running
interpreter, because the standard library's parser drifts between patch releases on
malformed markup. Dependencies are pinned by a lockfile. The container image, built from a
digest-pinned base with the lockfile, is the artifact that is actually frozen;
`pip install edgar-itemize==<version>` on a verified interpreter is the convenient
approximation. If an interpreter ever produces different output, the release notes say so
and the container is the reference.

---

--8<-- "docs/VERSIONING.md"
