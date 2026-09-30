# How the parser works

The flow from a raw submission file to the three tables, as diagrams. Module names refer to
files under `src/edgar_itemize/`, and `module.function` names the place to open in the
source. Every rule id mentioned here is described in the [catalogue](rules.md).

## From EDGAR to a citation

```mermaid
--8<-- "docs/diagrams/architecture-01-1-from-edgar-to-a-citation.mmd"
```

Three commands take a machine from the SEC's public index to a parsed corpus. Everything
a node says about a filing is a pair of byte offsets plus the label and provenance the
parser attached, so results are shareable without the filings, and `verify` proves that an
install reproduces the release.

## One document through the parser

`parse_document` is two halves. The first (load, split, classify, normalise) is the
cacheable prefix: it can be memoised on a digest of its own source, so a re-parse with a
warm cache runs only the second half and gets exactly what a fresh parse would. The second
half (`parse_prepared`) is the parser proper.

```mermaid
--8<-- "docs/diagrams/architecture-02-2-one-document-through-the-parser.mmd"
```

Two things the picture cannot show. Most of the passes between `find_candidates` and the
tree builders are *tags only*: they append a rule id to a candidate and move no score, and
exactly one later stage reads the tag. That is how a rule stays scoped to the corpus it was
measured on. And the order is load-bearing: a pass that reads another pass's tags runs
after it, and a pass whose tags nobody upstream reads runs before contents detection so
that every contents region is bit-identical with the pass on or off.

## Grammar routing

```mermaid
--8<-- "docs/diagrams/architecture-03-3-grammar-routing.mmd"
```

A grammar is a tuple of levels: a kind, a nominal depth, an anchored pattern, and whether
the level accepts only labels it knows (10-K Items) or may share its paragraph with body
text (contract Sections and clauses).

## The two tree builders

Both take the candidate list and the contents regions and return nodes and rejected rows.
Both select, per level, the maximum-weight strictly increasing subsequence of candidates by
grammar order: deterministic, no thresholds beyond a minimum score, tolerant of undetected
contents pages and of cross-references. Nothing is greedy.

### Form 10-K and 10-Q

```mermaid
--8<-- "docs/diagrams/architecture-04-4a-form-10-k-10-q-tree-build-tree.mmd"
```

### Contracts

```mermaid
--8<-- "docs/diagrams/architecture-05-4b-contracts-ex-10-kind-text-tree-contract-build.mmd"
```

Clauses are placed by a separate sequencer: a Viterbi pass over stack states with
deterministic costs for skipping an indicator, restarting a list, opening a level high, or
changing letter family, which replaces greedy descent because greedy descent cannot tell
"descend a level" from "missing indicator" from "spurious label" until it is too late.

## A candidate's life

What `rule_ids` on a node, or `reason` on a rejected row, is telling you, in the order the
tags arrive.

```mermaid
--8<-- "docs/diagrams/architecture-06-5-a-candidate-s-life.mmd"
```

A rejected row carries the *last* decision as its reason, from a closed vocabulary, and not
the tags that led there; a node carries every tag.

## Paths, segments and the back matter

```mermaid
--8<-- "docs/diagrams/architecture-07-6-paths-segments-and-the-back-matter.mmd"
```

After paths are assigned, every main-body span is clamped at the back-matter boundary, so
the last Item no longer runs over the signature page and the exhibit index; a sub-heading
that loses its parent's cover is re-parented, never re-spanned.

## A run

```mermaid
--8<-- "docs/diagrams/architecture-08-7-a-run-the-cli-workers-and-the-cache.mmd"
```

An exception in one document becomes a `documents` row with `error` set and no node rows;
the run continues. The tables are identical with the normalisation cache on or off, and
with any worker count or partitioning; only a sidecar file says which rows the cache
supplied.

## The objects

```mermaid
--8<-- "docs/diagrams/architecture-09-8-the-objects.mmd"
```

Node and rejected rows are written by `pipeline.result_rows`; the columns and their
promises are the [output contract](output-contract.md).

## Which stage reads what another wrote

Generated from the source with the [catalogue](rules.md): every edge is a rule id one stage
writes onto a candidate or node and a later stage tests for.

```mermaid
--8<-- "docs/diagrams/rules-01-which-stage-reads-what-another-wrote.mmd"
```
