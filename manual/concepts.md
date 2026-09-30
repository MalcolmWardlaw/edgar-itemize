# Concepts

What the output means, in the order a reader meets it. The precise statement of every
column is the [output contract](output-contract.md); this page is the vocabulary.

## The agenda tree

A filing has an agenda: the SEC's form prescribes Parts and Items for a 10-K or 10-Q, and a
credit agreement is drafted as Articles, Sections and lettered or numbered clauses. The
parser recovers that agenda as a tree. Each node is a heading; its children are the
headings nested under it; the document itself is the root.

| grammar | levels | used for |
|---|---|---|
| `form10k` | Part, Item (with suffixes `1A`, `7A`, ... and Regulation AB's `1100`-`1123`), sub-heading | 10-K, 10-K405, 10-KSB and the like; EX-13 annual reports |
| `form10q` | Part I and II, Item (numbering restarts in Part II), sub-heading | 10-Q, 10-QSB and the like |
| `contract` | Article, Section `a.bb`, clauses `(a)`, `(i)`, `(A)`, `(1)` | EX-10 exhibits, bare text contracts |

A 10-Q submission tagged as a 10-K in the SEC header is parsed with the 10-K grammar and
the node carries `gram.form_from_header`, because the per-document type tag is filer-supplied
and sometimes wrong.

`level_kind` names the level: `document`, `toc`, `part`, `item`, `heading`, `article`,
`section`, `clause_alpha`, `clause_roman`, `clause_upper`, `clause_upper_roman`,
`clause_num`. `label_canon` is the canonical label (`PART II`, `ITEM 7A`, `ARTICLE 7`,
`SECTION 7.01`, `(a)`), null for an unlabelled sub-heading; `label_raw` and `title` are as
written.

## Byte offsets

Every span is a pair of byte offsets into the raw full-submission `.txt` file exactly as the
SEC serves it. The parser decodes the file as latin-1 so that one character is one byte;
offsets never depend on the file's declared encoding, and `text[raw_start:raw_end]` of the
decoded file is the node's span, tags and all.

* `raw_start`, `raw_end`: the node's whole span, from its heading line to the start of the
  next node at the same or a shallower depth. A main-body node's span stops at the
  back-matter boundary, so the last Item of a 10-K does not run over the signature page and
  the exhibit index.
* `head_raw_start`, `head_raw_end`: the heading line only. A synthesised node (a Part the
  filing never printed, an Article implied by its Sections) has no printed heading and an
  empty head span.
* `norm_start`, `norm_end`: offsets into the parser's normalised text, for tooling. They are
  not part of the promise and are null under `--no-text`.

Offsets into a public filing are facts about that filing. A node table is shareable without
the filings, and every row is checkable by anyone who has the file.

## Paths and the meta digit

Every node carries a fixed-length `path` of eight ordinals. `path[0]` is the **meta** digit:
0 front matter, 1 a contents region, 2 the main body, 3 the back matter. `path[1:]` are
1-based ordinals of appearance among siblings at each depth, zero-padded:
`2.3.2.0.0.0.0.0` is the second child of the third top-level heading of the main body.

The digits are pure ordinals, never label numbers. Item numbering has gaps (`1A`, a reserved
`6`, Item 14 changing Part over the years) and clause schemes vary, so the label lives in
`label_canon` and the path says only *where* in the document's agenda the node sits. That is
what makes paths comparable across filings that number differently, and what makes the
tree fault-tolerant: a missing indicator shifts nothing but the ordinals after it.

Several agreements are sometimes glued into one EX-10 document. `segment` is 0 for the only
or first instrument and 1, 2, ... for each later one; each segment gets its own front, main
and back matter and its own top-level ordinals.

## Candidates, the chain and the rejected table

The parser does not look for Items one at a time. It first proposes every block whose text
starts with a label the grammar knows, scores each candidate on style and position and
penalises cross-reference phrasing and contents-page evidence, then selects, per level, the
**maximum-weight strictly increasing subsequence** of candidates in grammar order. That
global choice is deterministic, needs no thresholds beyond a minimum score, and is what
lets an undetected contents page or a cross-reference lose to the real heading: they carry
less weight, and the chain that includes the real heading outweighs the chain that includes
them.

Every candidate that does not become a node is written to the `rejected` table with the
last decision as its `reason`, a closed vocabulary: `toc` (a contents or index row),
`nonmonotone` (it does not fit the chain), `low_score`, `weak_duplicate`, and the contract
reasons `clause_nonmonotone` and `clause_outside_section`, among others. When an Item you
expect is missing, the rejected table usually shows the heading and says why it lost.

## Rule ids

`rule_ids` on a node is its provenance: every rule that proposed, scored, placed,
re-parented or tagged it, in the order they fired. Families: `gram` (how the label was
read), `sty` and `pos` (style and position evidence), `toc` (contents evidence), `rej`
(penalties and vetoes applied), `seq` (sequencing decisions), `chain` and `seg` (numbering
restarts and instrument boundaries in contracts), `agenda` and `tree` (placement and span
changes), `kind` (EX-13 classification), `multi.ITEM <k>` (the further Items a combined
heading covers). The vocabulary is open: a minor version may add an id or retire one. The
[catalogue](rules.md) lists every id at this version with the module that writes it and the
module that reads it.

`confidence` is the candidate's score, in [0, 1]; the document root has 1.0, a contents
node 0.8. It is a within-parser ordering, not a calibrated probability.

## Legitimately missing Items

Many "missing" Items are properties of the filing, not parser errors, and a study should
decide how to treat them before suspecting the parser:

* **asset-backed trusts** file 10-Ks under Regulation AB with Items 1112 to 1123 in place of
  the ordinary ones (the parser recovers those items; `profile` signals flag the form);
* **incorporation by reference**: an Item whose text is a one-line pointer to the annual
  report or proxy statement. The node exists, is tiny, and carries the
  `item_incorporated_by_reference` and `ibr_target` flags;
* **omission statements** ("Item 6. Not applicable") and small filers that skip Items;
* **tiny or header-only submissions**, and scanned exhibits (page images with a hidden text
  layer, era `image_text`), which yield little or no structure.

The `documents` table's `items_found` list and the `profile_era` column are the first
things to look at when a filing's tree looks thin.

## What is out of scope

No XBRL, no table extraction, no text cleaning. The parser addresses text; it does not
extract or transform it. Pair it with whatever text-processing you already use, applied to
the spans it returns.
