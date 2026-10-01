# edgar-itemize

edgar-itemize turns the raw filings in the SEC's EDGAR archive into a table of their
headings. For each filing it recovers the agenda the document is written to:

* Form 10-K and 10-Q bodies: Part, Item, sub-heading;
* credit and loan agreements filed as EX-10 exhibits: Article, Section, (a), (i), (A), (1);
* EX-13 annual reports: the headings that stand in for a 10-K's Items 6, 7 and 8.

Each heading is one row that records where its section starts and ends as **byte offsets
into the submission file exactly as the SEC serves it**, so the text of any section, from
Item 1A down to a single covenant clause, is one slice of a file you already have. It reads
the whole EDGAR era, from 1993 plain text through 2000s publisher HTML to inline XBRL, with
one grammar per form rather than one per layout. [Concepts](concepts.md) explains what a
node is and why offsets, in plain terms.

## Why this exists

* **Deterministic.** The parser is a fixed set of written rules, not a language model
  deciding over a corpus. There is no machine learning inside it and no randomness: the
  same release on the same file gives the same rows on any machine. Every heading names the
  rules that produced it, and every candidate heading that was dropped is written out with
  the reason, so a reader can enumerate the population any rule produced.
* **Replicable by hash, with the data untouched.** Each tagged release gives exactly the
  same results on exactly the same EDGAR files, checked by SHA-256 of the input and of the
  output. The filings are never modified or redistributed, so nobody has to archive
  gigabytes of derived text as validation, and every value traces directly to a byte range
  in a public file. That is what lets a paper say *"we pulled Items 1, 7 and 8 with
  edgar-itemize v1.0.0"* and mean something checkable ([reproducibility](reproducibility.md)).
* **The whole agenda, down to the clauses.** Most tools pull out a few top-level sections.
  10-Ks, and the contracts attached as EX-10 exhibits even more, have deep agenda
  structures: a study of covenants needs the affirmative and negative covenant Articles and
  each clause one or two levels beneath them. The tree goes to that depth, with ordinal
  paths at a fixed depth so trees compare across filings that number differently.
* **Your corpus, downloaded once, parsed locally.** You pull as much or as little of EDGAR
  as you want, up to the whole archive, and parse it on your own machine. Rate-limited SEC
  downloads are slow but happen once; storage is cheap; re-parsing with different choices
  costs nothing but compute. The 1993 to 2000 plain-text and SGML filings, where HTML-based
  tools degrade by construction, are parsed with the same grammars as the rest.
* **Improves by frozen tagged releases.** The output is evaluated by human review and by
  language-model judges, and the evaluation feeds hand-written rule changes; nothing a
  model says enters the parser. Current results are good, with room left
  ([evaluation](evaluation.md)). Each improvement ships as a new tagged release that keeps
  the two guarantees above; earlier releases stay as they were.
* **Public and updatable.** The author keeps working on it. Anyone who finds a set of
  errors can package them and send them in ([contributing](contributing.md)), and they are
  folded into the next release.

Where the parser stands against the other open tools, with numbers, is in
[evaluation](evaluation.md) and [related tools](related.md).

## What to do with it

**Get the sections.** [Install](install.md), then follow the [quickstart](quickstart.md):
a manifest from the SEC's index, the raw files, a parse, and the sections you want pulled
into one parquet with their text (`examples/pull_items.py` is in the repository):

```
edgar-itemize manifest --years 2019-2020 --no-only-present --out manifest.parquet
edgar-itemize fetch --manifest manifest.parquet
edgar-itemize parse --manifest manifest.parquet --out run_10k --kind 10k --no-text --partition-by year
python examples/pull_items.py --run run_10k --items "ITEM 1A,ITEM 7" --out items.parquet
```

For credit agreements, parse EX-10 exhibits with `--kind ex10` and select the Articles whose
title names the covenants; the quickstart's last step shows how.

**Check a result or a paper's claim.** Confirm that your install reproduces the release,
then compare a full run of your own with the per-corpus hash parquet published with the
release ([reproducibility](reproducibility.md)):

```
edgar-itemize verify --data-root /path/to/edgar
python examples/check_hashes.py --run run_10k --expected hashes_1.0.0_10k.parquet
```

**Help.** Report a wrong parse as an issue with the accession number, the document
sequence, the edgar-itemize version, and the byte offsets of what the parser produced and
what you expected. A set of verdicts from your own review is welcome the same way; a file
format for exchanging verdicts is planned for release 1.0.1 ([contributing](contributing.md)).

To see the output before installing anything, try the [live demo](demo-guide.md).

## What you get

Three parquet tables per run, described column by column in the [output contract](output-contract.md):

| table | one row per | what it holds |
|---|---|---|
| `nodes` | heading in the tree | label, title, byte span, ordinal path, confidence, the rule ids that produced it |
| `documents` | parsed document | era and publisher profile, counts, the items found, the input file's SHA-256 |
| `rejected` | heading candidate that was dropped | where it was, its score, and the reason |

## Where to read next

1. [Concepts](concepts.md) before using the output in earnest: what a node and a path mean,
   where a span ends, why an Item can be legitimately absent.
2. [How the parser works](architecture.md) has the flow as diagrams, for readers who want
   to know why a particular node exists.
3. [Reproducibility and versions](reproducibility.md) for what a release promises and how
   to check it.

## Citing

Name the version you ran. The citation metadata is in the repository's `CITATION.cff`;
see [citing](citing.md).
