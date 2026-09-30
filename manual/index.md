# edgar-itemize

edgar-itemize is a deterministic, rule-based parser that recovers the **agenda structure**
of SEC EDGAR filings and addresses every heading by **byte offset into the raw
full-submission file**:

* Form 10-K and 10-Q bodies: Part, Item, sub-heading;
* credit and loan agreements filed as EX-10 exhibits: Article, Section, (a), (i), (A), (1);
* EX-13 annual reports: the headings that stand in for a 10-K's Items 6, 7 and 8.

It works across the whole EDGAR era, from 1993 plain text through 2000s publisher HTML to
inline XBRL, with one grammar per form rather than one per layout. There is no machine
learning inside the parser and no randomness: the same release on the same bytes gives the
same rows on any machine, which is what lets a paper say *"we pulled Items 1, 7 and 8 with
edgar-itemize v1.0.0"* and mean something checkable.

## What you get

Three parquet tables per run, described column by column in the [output contract](output-contract.md):

| table | one row per | what it holds |
|---|---|---|
| `nodes` | heading in the tree | label, title, byte span, ordinal path, confidence, the rule ids that produced it |
| `documents` | parsed document | era and publisher profile, counts, the items found, the input file's SHA-256 |
| `rejected` | heading candidate that was dropped | where it was, its score, and the reason |

Every node is a pair of offsets into a public filing plus the label the parser attached, so
a node table can be shared without redistributing filings, and anyone with the filing can
check any row by slicing the file.

## Why it exists

Item extraction for modern HTML 10-Ks is done by several open tools. edgar-itemize is for
the cases they do not cover and the guarantee they do not make:

* **the long panel**: 1993 to 2000 filings are plain text and SGML, where HTML-based tools
  degrade by construction;
* **the tree below the Items**: sub-headings inside an Item, and Article, Section and clause
  inside a contract, with ordinal paths at a fixed depth;
* **provenance**: every node says which rules produced it and every rejected candidate is
  written out with its reason, so a reader can enumerate the population a rule produced;
* **a frozen reference**: tagged releases are never changed, an install can be checked
  against a shipped conformance set, and the environment is pinned down to a container
  digest ([reproducibility](reproducibility.md)).

Where the parser stands against the other tools, with numbers, is in
[evaluation](evaluation.md) and [related tools](related.md).

## Where to start

1. [Install](install.md) the package or pull the container.
2. Follow the [quickstart](quickstart.md): a manifest from the SEC's public index, the raw
   files, a parse, and a first look at the tables.
3. Read [concepts](concepts.md) before using the output in earnest: what a path means,
   where a span ends, why an Item can be legitimately absent.
4. [How the parser works](architecture.md) has the flow as diagrams, for readers who want
   to know why a particular node exists.

## Citing

Name the version you ran. The citation metadata is in the repository's `CITATION.cff`;
see [citing](citing.md).
