# Contributing to edgar-itemize

edgar-itemize is a reference implementation: a frozen, deterministic, citable parser. That
shapes what contributions look like. The governance is `docs/RELEASE_PLAN.md` section 7; the
version policy is `docs/VERSIONING.md`; the output it protects is `docs/OUTPUT_CONTRACT.md`.

## Reporting a wrong parse

Open an issue with:

1. the **accession number** (dashed, `0000893220-04-000596`) and the **document sequence**
   of the `<DOCUMENT>` block (the `sequence` column; the primary document of a 10-K is
   usually 1);
2. the **edgar-itemize version** you ran: `edgar-itemize --version`, or the
   `parser_version` column of the output; and, if you can, the first line of `edgar-itemize verify` on
   that install, which says whether your build reproduces the release's conformance set
   (`docs/VERSIONING.md` section 6);
3. what the parser produced (the node or rejected row: `label_canon`, `raw_start`,
   `rejected.reason` if a heading was dropped);
4. what you **expected**, with the **byte offsets** of the heading in the raw submission
   file or, failing that, the heading text as it appears there.

Before suspecting the parser, check the filing: many "missing items" are real properties of
the filing (asset-backed trusts under Regulation AB, incorporation-by-reference stubs,
omission statements, tiny or header-only submissions). A confirmed wrong parse goes into
`ERRATA.md` against the version that produced it; the fix ships in the next minor. Released
versions are never changed.

## Pull requests

Welcome, and reviewed promptly, for:

* bugs that do not change tree behaviour (crashes, encoding, CLI, packaging);
* documentation;
* packaging, CI, the container image;
* the input tooling: manifests from the public index, the fetch helper, `verify`.

Before opening a PR:

```
uv sync --all-extras
uv run pytest -q
```

Every test must pass (one `xfail` is expected). Python 3.11+ via `uv`; no other package
manager. To check that a build reproduces the release on a mirror of the filings, run
`edgar-itemize verify --data-root DIR --workers 8`; its one-line verdict is the build check.

## Changes to tree behaviour

A change that adds, removes or moves a node, or changes a rejection reason, is a
tree-behaviour change, and it enters only through the gate:

1. a full before/after `scripts/run_diff.py` on every corpus the change can reach (10-K,
   10-Q, EX-10, EX-13, and the private contracts-text corpus), with the losses enumerated one by one and
   inspected, not sampled. No exception for changes that look obviously safe: several
   regressions in this project's history were caught only by the gate;
2. for a new rule, a judged out-of-sample bank: windows drawn from filings the rule was not
   designed on, rated before the rule ships, with the rule's precision on them reported;
3. a decision memo with every number from a stored artifact named next to it.

Tree changes from outside are run through this gate by the maintainers. Open the PR against
`main` with the rule id(s), the corpora it touches and, if you have one, the run_diff of a
sample; we run the full gate, read the losses and merge or return it with the read. Expect
that to take days, not hours: a full 10-K parse is about three hours and the 10-Q about five.

A merged tree change is a minor release and gets a `CHANGELOG.md` entry naming its gate.

## Rules that do not bend

* **No language-model output enters the parser.** Judges, banks and provisional labels are
  evaluation only. A rule is written by hand, from an enumerated population, and measured.
* **Every node is explained.** A new rule writes its id into `rule_ids` on every node it
  produces or tags, and every candidate it drops gets a `rejected` row with a reason from
  the closed vocabulary (`docs/OUTPUT_CONTRACT.md` section 7.6). A new reason is a major
  version.
* **Determinism.** No randomness, no dependence on worker count, partitioning, dictionary
  order or environment. Sampling is deterministic (keyed hashes, no random number generator).
* **The name.** A modified build must not present itself as a numbered edgar-itemize
  release. If you fork and change tree behaviour, change the package version string to
  something that is not a release tag (`1.2.0+yourlab`) so that rows stamped by your build
  are not mistaken for ours. The MIT licence allows the fork; this asks only that the
  version column stays honest.

## Layout

`src/edgar_itemize/` is the parser (`sgml.py` -> `classify.py` -> `normalize_*.py` ->
`candidates.py` -> `toc.py` -> `tree.py` / `tree_contract.py` -> `headings.py`;
`pipeline.py` wires them; `grammar/` holds the label grammars). `tests/` is pytest.
`scripts/` holds the corpus manifests, `run_diff.py` and the evaluation tooling. `gold/`
holds hand-reviewed labels. The viewer (`edgar_itemize.viewer`, extra `[viewer]`) and the
judge client (`edgar_itemize.llm`, extra `[judge]`) are not covered by the output promise.
