# edgar-itemize output contract

This is the document a citing paper points at. It states what `edgar-itemize parse` writes,
what every column means, which value vocabularies are closed, and what the version number
promises about all of it. The policy behind the promise is `docs/VERSIONING.md`.

A `runs/...` name beside a number is the lab's identifier for the stored artifact the number
was read from; the artifacts are not in this repository, and the curated evaluation record
that resolves them ships with release 1.0.1 (`docs/RELEASE_PLAN.md` decision D3).

Everything here is read off the Turn 13 baselines of record and the source, not from memory:

* schemas and dtypes: `src/edgar_itemize/schema.py` (`NODE_SCHEMA`, `DOC_SCHEMA`,
  `REJECTED_SCHEMA`), checked against `runs/full_v30/{nodes,documents,rejected}-10k-2020.parquet`
  (930,538 node rows, 6,508 document rows, 197,336 rejected rows in that partition);
* row construction: `src/edgar_itemize/pipeline.py` (`result_rows`, `covers_items_of`,
  `parse_prepared`), `src/edgar_itemize/tree.py` (`Node`, `Rejected`, `build_tree`),
  `src/edgar_itemize/tree_contract.py` (`build_contract_tree`), `src/edgar_itemize/agenda.py`
  (`assign_paths`, `path_str`, `level_profile`, `PATH_LEN`), `src/edgar_itemize/classify.py`
  (`classify`, `ibr_flags`), `src/edgar_itemize/sgml.py` (`DocumentBlock`, `read_submission`),
  `src/edgar_itemize/writer.py` (`write_run`);
* label grammars: `src/edgar_itemize/grammar/{form10k,form10q,contract}.py`;
* value vocabularies: a census over every partition of `runs/full_v30` (10-K),
  `runs/full_10q_v11` (10-Q), `runs/full_ex10_v24` (EX-10) and `runs/full_ex13_v12` (EX-13),
  stored as `runs/judge/r0-contract-vocab.txt` and `runs/judge/r0-contract-vocab2.txt`
  (scripts `runs/judge/r0-contract-vocab.py`, `r0-contract-vocab2.py`).

The three columns `documents.input_sha256`, `documents.input_bytes` and
`documents.manifest_cik` are added in release 1.0.0 (`docs/RELEASE_PLAN.md` section 3 item 2
and decision D9) and are not in `runs/full_v30`; every other column below is exactly as that
run holds it, except that `cik` (in `nodes` and `documents`) is read from the file's own
header from 1.0.0 (D9; the manifest's value in every pre-release run).

## 1. Files

A run directory holds three parquet tables, each split into partitions:

| file | one row per |
|---|---|
| `nodes-<part>.parquet` | node of the agenda tree (the document root included) |
| `documents-<part>.parquet` | parsed document (one `<DOCUMENT>` block of one submission), including documents that failed |
| `rejected-<part>.parquet` | heading candidate that was considered and dropped |

`<part>` is `<kind>-<value>` when `parse --partition-by <column>` is used (the value is the
manifest column, normally `year`), else `all` (`cli.py`, `writer.write_run`). Rows are sorted
by `(accession_number, sequence, node_id)` in `nodes`, `(accession_number, sequence)` in
`documents` and `(accession_number, sequence, raw_start)` in `rejected`; the files are
written with zstd compression, no column statistics and empty schema metadata, so two runs
of the same code on the same inputs are byte-identical files (`writer.py`).

`--no-text` leaves `documents.normalized_text` null. Every baseline of record is written
that way (project rule 3), and the promise in section 8 is stated over runs written that way.

A document is identified across the three tables by `(accession_number, sequence)`.

## 2. `nodes` (36 columns)

| # | column | type | meaning |
|---|---|---|---|
| 1 | `accession_number` | string | SEC accession number, dashed form (`0000893220-04-000596`) |
| 2 | `cik` | string | filer CIK as a string of digits with no zero padding (`0000353944` -> `353944`). From 1.0.0 (decision D9, `docs/RELEASE_PLAN.md` section 9) it is the first FILER's `CENTRAL INDEX KEY` in the submission's own SGML header (`sgml.header_cik`), so it is a function of the input bytes: a co-registrant filing listed under several CIK directories gets the same `cik` from every copy. For headerless input (`--kind text`) it falls back to the manifest's `cik`. Before 1.0.0 it was the manifest value (`str(cik)`); the two differ on about 1.4% of 10-K filings (D9's probe, 182/13,168 for 2019-2020). The manifest's value is kept as `documents.manifest_cik` |
| 3 | `sequence` | int32 | `<SEQUENCE>` of the `<DOCUMENT>` block inside the submission; 0 when the block has none (`result_rows`: `r.doc.sequence or 0`) |
| 4 | `doc_type` | string | the block's `<TYPE>` tag as filed (`10-K`, `10-K405`, `10-Q`, `EX-10.1`, `EX-13`, ...) |
| 5 | `grammar` | string | the label grammar that built this tree: `form10k`, `form10q` or `contract` (section 7.1) |
| 6 | `node_id` | int32 | node number within the document, pre-order; the document root is 0 |
| 7 | `parent_id` | int32 | `node_id` of the parent; -1 on the root |
| 8 | `depth` | int8 | tree depth: 0 on the root, parent's depth + 1 elsewhere. Observed range 0..6 on the 10-K and 0..8 on EX-10 (`r0-contract-vocab2.txt`) |
| 9 | `level_kind` | string | what kind of heading the node is (closed vocabulary per grammar, section 7.2) |
| 10 | `label_canon` | string | the canonical label (`PART II`, `ITEM 7A`, `ITEM I.2`, `ARTICLE 7`, `SECTION 7.01`, `(a)`, `TOC`); null on the root, on every `heading` node and on a synthetic contract node without a label (section 7.3) |
| 11 | `label_raw` | string | the label as printed in the filing; null where no label was matched |
| 12 | `title` | string | the heading's title text as printed, cleaned of leaders and page numbers; up to 200 characters on `heading` nodes |
| 13 | `raw_start` | int64 | byte offset where the node's span starts in the raw submission file (section 5) |
| 14 | `raw_end` | int64 | byte offset just past the node's span |
| 15 | `head_raw_start` | int64 | byte offset where the heading line starts |
| 16 | `head_raw_end` | int64 | byte offset just past the heading line; equals `head_raw_start` on a synthetic node with no printed heading |
| 17 | `norm_start` | int64 | start of the node's span in the normalised text (section 5; not part of the promise) |
| 18 | `norm_end` | int64 | end of the node's span in the normalised text |
| 19 | `order_key` | int32 | the grammar's ordinal for the label, used for the monotone-sequence chain (section 6.4); 0 on the root, on `toc`, `heading` and clause nodes |
| 20 | `confidence` | float32 | the accepted candidate's score, capped at 1.0 (section 8.1) |
| 21 | `rule_ids` | list<string> | ids of the rules that produced or tagged the node (section 8.2) |
| 22 | `path` | list<int16> | the fixed-length ordinal path, always 8 entries (section 6.1) |
| 23 | `path_str` | string | `path` joined with dots (`2.3.2.0.0.0.0.0`) |
| 24 | `meta` | int8 | `path[0]`: which meta region of the document the node sits in (section 6.2) |
| 25 | `segment` | int8 | which glued instrument of a compound exhibit the node belongs to; 0 for the only or primary agreement (section 6.3) |
| 26 | `item_incorporated_by_reference` | bool | 10-K/10-Q item nodes with a stub-sized span only: the item's content is incorporated by reference from outside the document (section 8.5); null elsewhere |
| 27 | `ibr_target` | string | `ex13` or `annual_report` when the flag above is true; null otherwise |
| 28 | `ibr_target_in_submission` | bool | when the flag is true: whether the same submission carries an `EX-13*` document; null otherwise |
| 29 | `item_cross_reference` | bool | same population as 26: the stub points elsewhere inside this document rather than outside it; null elsewhere |
| 30 | `satisfies_item` | string | EX-13 `heading` nodes only: `ITEM 6`, `ITEM 7` or `ITEM 8` when the heading's content stands in for that 10-K item (section 8.3); null elsewhere |
| 31 | `covers_items` | list<string> | on a multi-item heading (`Items 1 and 2. Business and Properties`): the additional item keys it covers (`["2"]`); null otherwise |
| 32 | `profile_era` | string | the document's format era (section 7.5) |
| 33 | `profile_publisher` | string | the filer-agent family the submission came from (section 7.5) |
| 34 | `agent_cik` | string | the first ten characters of the accession number: the CIK of the filing agent |
| 35 | `parser_version` | string | the package version that wrote the row (`docs/VERSIONING.md`) |
| 36 | `normalizer_version` | string | the same package version; kept as its own column for schema stability |

Columns 1-5 and 32-36 are copied onto every node of a document from that document's
row (`result_rows`, `common`).

The root node (`node_id` 0, `level_kind` `document`) spans the whole `<TEXT>` payload,
carries `confidence` 1.0 and `rule_ids` `["doc"]` (`tree.py`, `build_tree`).

## 3. `documents` (25 columns, plus 3 in 1.0.0: 28)

| # | column | type | meaning |
|---|---|---|---|
| 1 | `accession_number` | string | as in `nodes` |
| 2 | `cik` | string | as in `nodes` |
| 3 | `sequence` | int32 | as in `nodes`; null on an error row |
| 4 | `doc_type` | string | as in `nodes` |
| 5 | `grammar` | string | as in `nodes` |
| 6 | `filed_year` | int32 | the manifest's `year` column (the filing year) |
| 7 | `profile_era` | string | as in `nodes` |
| 8 | `profile_publisher` | string | as in `nodes` |
| 9 | `agent_cik` | string | as in `nodes` |
| 10 | `signals` | string | JSON object: the counts of the format fingerprints `classify` measured on the payload (`workiva_comment`, `page_mark`, `reg_ab`, `hidden_white_text`, ... ; the key set is `classify._SIGNALS` plus `reg_ab_specific`, `reg_ab_generic`, `reg_ab_name`). Diagnostic, not part of the promise |
| 11 | `n_blocks` | int32 | number of normalised text blocks |
| 12 | `n_candidates` | int32 | number of heading candidates proposed |
| 13 | `n_nodes` | int32 | number of rows this document has in `nodes` |
| 14 | `toc_found` | bool | at least one table-of-contents region was detected |
| 15 | `items_found` | list<string> | the `label_canon` of every `item` node in document order, followed by the items credited through `covers_items`, de-duplicated (`result_rows`) |
| 16 | `doc_raw_start` | int64 | byte offset of the first character inside the block's `<TEXT>` element (`DocumentBlock.text_start`) |
| 17 | `doc_raw_end` | int64 | byte offset just past the last character inside `<TEXT>` |
| 18 | `front_end` | int64 | byte offset where front matter ends: the `raw_start` of the first node (`assign_paths`, `_bounds.front_end`) |
| 19 | `back_start` | int64 | byte offset where back matter begins (signature page, exhibit index, financial appendix); `doc_raw_end` when none was found (`agenda._back_matter_boundary`). On a compound exhibit, the last segment's boundary |
| 20 | `n_segments` | int32 | number of segments (section 6.3); 1 for every 10-K, 10-Q and EX-13 document |
| 21 | `level_profile` | string | JSON list, one entry per occupied path position in the main body: `{"position", "dominant", "kinds", "n"}` (`agenda.level_profile`). Diagnostic, not part of the promise |
| 22 | `normalized_text` | large_string | the normalised text the `norm_*` offsets index; null under `--no-text` (every baseline) |
| 23 | `parser_version` | string | as in `nodes` |
| 24 | `normalizer_version` | string | as in `nodes` |
| 25 | `error` | string | null when the parse succeeded. Otherwise the reason (`no_primary_document`, or the exception type and message); columns 1, 2, 6 and 28 are set (on an error row `cik` is the manifest's value, the file having yielded no header) and every other column is null, and the document has no `nodes` or `rejected` rows. `runs/full_v30` holds 17 such rows, all `no_primary_document` (`r0-contract-vocab2.txt`) |
| 26 | `input_sha256` | string | hex SHA-256 of the raw full-submission `.txt` file the document was read from (added in 1.0.0) |
| 27 | `input_bytes` | int64 | size in bytes of that file (added in 1.0.0) |
| 28 | `manifest_cik` | string | the CIK the manifest listed for this row, i.e. the `<CIK>` directory of the mirror the file was read from (`archives/edgar/data/<CIK>/<accession>.txt`), unpadded (added in 1.0.0, decision D9). Equal to `cik` except on co-registrant filings whose header's first FILER is another registrant; use it, not `cik`, to locate the file in a mirror. Always set, on error rows too |

`input_sha256` is what the reproducibility promise is stated over (`docs/VERSIONING.md`):
two runs of the same release on inputs with the same hash produce the same `nodes` and
`rejected` rows, including their `cik`, which is read from the same bytes. `nodes` and
`rejected` do not carry the hash; join on `(accession_number, sequence)`.

## 4. `rejected` (9 columns)

| # | column | type | meaning |
|---|---|---|---|
| 1 | `accession_number` | string | as in `nodes` |
| 2 | `sequence` | int32 | as in `nodes` |
| 3 | `block_idx` | int32 | index of the normalised block the candidate came from |
| 4 | `kind` | string | the candidate's level kind: `part`, `item`, `article`, `section`, `clause` or `heading` (section 7.6) |
| 5 | `label_canon` | string | the candidate's canonical label; empty string on a `heading` |
| 6 | `score` | float32 | the candidate's score at the time it was dropped (the node's `confidence` on a `heading`) |
| 7 | `reason` | string | why it was dropped (closed vocabulary, section 7.6) |
| 8 | `raw_start` | int64 | byte offset of the candidate's heading line (`head_raw_start`; the block's `raw_start` on a `heading`) |
| 9 | `text` | string | the first 120 characters of the block's text |

Every candidate that was considered and not placed is written here with its reason, so
the accepted tree and the rejected table together account for every candidate
(`tree.py` and `tree_contract.py`, `rej()`; `headings.py` for `rej.page_banner`).

## 5. Offsets

All `raw_*` and `head_raw_*` columns, `doc_raw_start`, `doc_raw_end`, `front_end`,
`back_start` and `rejected.raw_start` are **byte offsets into the raw full-submission
`.txt` file**, the file at `.../edgar/data/<CIK>/<accession>.txt` exactly as the SEC serves
it. The parser decodes that file as latin-1 so that one character is one byte
(`sgml.read_submission`); offsets are therefore both character offsets into that decoded
string and byte offsets into the file, and they never depend on the file's declared
encoding. `text[raw_start:raw_end]` of the latin-1 string is the node's span, tags and all.

* `raw_start`/`raw_end` bound the node's whole span: from its heading line to the start of
  the next node at the same or a shallower depth, or to the parent's end. A main-body
  node's span is cut at `back_start` (`tree.truncate_at_back_start`, rule
  `tree.back_truncate`), so the last item of a 10-K does not run over the signature page
  and the exhibit index. A `toc` node spans the detected contents region.
* `head_raw_start`/`head_raw_end` bound the heading line only. On a synthetic node
  (`gram.synth_part`, `gram.synth_article`, `gram.synth_section`, ...) there is no printed
  heading and `head_raw_end == head_raw_start == raw_start`.
* `norm_start`/`norm_end` index `documents.normalized_text`, the era-agnostic text the
  candidates were found in. They are provided for tooling and are **not part of the
  promise**: the normaliser may change between minor versions, and the column is null
  under `--no-text`. The HTML normaliser tokenises with a parser vendored from CPython
  3.11.15 (`edgar_itemize._html_parser`), so what it produces from malformed markup does
  not depend on the interpreter's `html.parser` (`docs/VERSIONING.md` section 5).

Offsets into a public filing are facts about that filing; a node table can be shared
without redistributing the filing.

## 6. Paths, meta, segment, order

### 6.1 `path` and `path_str`

Every node carries a fixed-length path of `PATH_LEN = 8` ordinals (`agenda.py`), stored as
`path` (list of 8 int16) and `path_str` (the same joined with dots). `path[0]` is the meta
region (below); `path[1:]` are 1-based ordinals of appearance among siblings at each depth,
zero-padded: `[2, 3, 2, 0, 0, 0, 0, 0]` is the second child of the third top-level heading of
the main body. The digits are pure ordinals of appearance, not label numbers: item numbering
has gaps (`1A`, a reserved `6`, `14` changing Part) and clause schemes vary, so the label
lives in `label_canon` and never in the path. Ordinals are assigned per meta region and, on a
compound exhibit, restart at 1 in every segment (`assign_paths`).

The depth is global and fixed: a node deeper than seven levels below the root (depth 8, seen
on EX-10) inherits its parent's path rather than extending it (`assign_paths`, `walk`). The
root's path is `[2, 0, 0, 0, 0, 0, 0, 0]`; a `toc` node's is `[1, i, 0, ...]` for the i-th
contents region.

### 6.2 `meta`

`meta = path[0]`, with `META_FRONT, META_TOC, META_MAIN, META_BACK = 0, 1, 2, 3`
(`agenda.py`). A node is `3` when its `raw_start` is at or past its segment's back-matter
boundary and `2` otherwise; `toc` nodes are `1`. Front matter (`0`) is everything before
`front_end` and holds no node: the census finds only the values 1, 2 and 3 on every corpus
(10-K: 162,011 / 23,205,640 / 2,991,134 nodes; EX-10: 18,631 / 5,557,548 / 344,258;
`r0-contract-vocab2.txt`).

### 6.3 `segment`

Several agreements are sometimes glued into one EX-10 document. `segment` is 0 for the
only or primary instrument and 1, 2, ... for each later instrument that begins at a numbering
restart carrying instrument evidence (`seg.instrument_boundary`; `agenda.chain_restarts`,
`compound_restarts`). Each segment gets its own front/main/back computation and its own
top-level ordinals. A numbering restart without that evidence (`chain.restart_unsegmented`)
starts a new chain for sequencing but not a new segment. `segment` is capped by
`MAX_SEGMENTS = 32`; the EX-10 baseline holds values 0..3 (5,896,459 / 22,454 / 763 / 761
nodes). It is always 0 on the 10-K, 10-Q and EX-13 (`n_segments` 1 on every one of their
documents).

### 6.4 `order_key`

The grammar's ordinal for a label, on which the tree builder runs a maximum-weight strictly
increasing subsequence per level (`tree.max_weight_increasing`):

* `form10k`: `PART I..V` -> 1..5; `ITEM <n><suffix>` -> `n * 10 + {"": 0, A: 1, B: 2, C: 3}`
  (`ITEM 7A` -> 71, `ITEM 1122` -> 11220);
* `form10q`: `PART I/II` -> 1/2; items -> `{I: 0, II: 100} + n * 10 + suffix` (`ITEM II.1A` -> 111);
* `contract`: `ARTICLE n` -> n; `SECTION a.bb[.cc]` -> `a * 10000 + bb * 100 + cc`; clauses 0
  (ordered by the clause sequencer, not by this key).

`toc`, `heading` and the root carry 0.

## 7. Vocabularies

Closed vocabularies, from the census (`runs/judge/r0-contract-vocab.txt` unless noted).
The `grammar` column, not the run a row came from, says which vocabulary applies: the
10-Q baseline holds 850 nodes routed to `form10k` and 142 to `contract` by the
submission's header and the block's `<TYPE>` (`pipeline.grammar_for`).

### 7.1 `grammar`

`form10k` (Form 10-K bodies and, with `--kind ex13`, EX-13 annual reports), `form10q`
(Form 10-Q bodies), `contract` (EX-10 exhibits and `--kind text` inputs).

### 7.2 `level_kind`

| grammar | values (count in the baseline) |
|---|---|
| `form10k`, 10-K (`runs/full_v30`, 26,358,785 nodes) | `heading` 20,761,539; `item` 4,283,694; `part` 922,755; `document` 228,786; `toc` 162,011 |
| `form10k`, EX-13 (`runs/full_ex13_v12`, 818,640 nodes) | `heading` 804,944; `document` 12,979; `item` 425; `part` 282; `toc` 10 |
| `form10q` (`runs/full_10q_v11`, 34,082,208 `form10q` nodes) | `heading` 25,584,317; `item` 5,799,505; `part` 1,443,371; `document` 736,751; `toc` 519,117 |
| `contract` (`runs/full_ex10_v24`, 5,920,437 nodes) | `clause_alpha` 2,603,979; `section` 1,814,903; `clause_roman` 904,783; `article` 258,039; `clause_upper` 163,459; `clause_num` 133,784; `toc` 18,631; `document` 17,371; `clause_upper_roman` 5,488 |

So the full vocabulary is `document`, `toc`, `part`, `item`, `heading`, `article`,
`section`, `clause_alpha`, `clause_roman`, `clause_upper`, `clause_upper_roman`,
`clause_num`. The clause kinds are the marker family the sequencer resolved: `(a)` alpha,
`(i)` roman, `(A)` upper, `(I)` upper roman, `(1)` numeric (`tree_contract._KIND`).

### 7.3 `label_canon`

`null` means an unlabelled heading: every `heading` node, the root, and a synthetic contract
node built to hold clauses that have no accepted section (`gram.synth_section`, with
`gram.synth_section_xref` on the ones a vetoed cross-reference orphaned: 1,600 sections and
1,144 articles in the EX-10 baseline, `runs/judge/r0-contract-vocab.txt`). Every `part`, `item` and
`toc` node is labelled.

**`form10k`** at `depth <= 2` in `runs/full_v30`: `PART I` 227,992; `PART II` 228,225;
`PART III` 227,012; `PART IV` 227,564; `PART V` 11,962; `ITEM 1` 225,335; `ITEM 1A` 151,674;
`ITEM 1B` 144,208; `ITEM 1C` 18,041; `ITEM 2` 222,973; `ITEM 3` 226,560; `ITEM 4` 222,069;
`ITEM 5` 226,621; `ITEM 6` 220,480; `ITEM 7` 225,125; `ITEM 7A` 193,373; `ITEM 8` 224,707;
`ITEM 9` 224,207; `ITEM 9A` 170,984; `ITEM 9B` 157,399; `ITEM 9C` 29,097; `ITEM 10` 220,474;
`ITEM 11` 217,466; `ITEM 12` 218,514; `ITEM 13` 218,267; `ITEM 14` 221,650; `ITEM 15` 180,233;
`ITEM 16` 44,769; `ITEM 1112` 11,762; `ITEM 1114` 10,247; `ITEM 1115` 9,683; `ITEM 1117` 11,932;
`ITEM 1119` 11,950; `ITEM 1122` 11,948; `ITEM 1123` 11,946; `TOC` 162,011; null 2,310,282.

That is `PART I`..`PART IV` as printed, `PART V` (synthetic: the Part the Regulation AB items
are placed under, never matched from text; `form10k.py`, `order_key`), `ITEM 1`..`ITEM 16`
with the letter suffixes `1A`, `1B`, `1C`, `7A`, `9A`, `9B`, `9C` (`form10k.ITEM_TITLES`), the
seven Regulation AB items `1112`, `1114`, `1115`, `1117`, `1119`, `1122`, `1123`
(`form10k.REGAB_TITLES`; the other Reg-AB numbers are not accepted), and `TOC`. The EX-13
corpus uses the same grammar and vocabulary; its 425 items and 282 parts are the rare
annual report that reproduces 10-K headings.

**`form10q`** at `depth <= 2` in `runs/full_10q_v11`: `PART I` 716,663; `PART II` 726,703;
`ITEM I.1` 683,931; `ITEM I.2` 688,862; `ITEM I.3` 548,416; `ITEM I.4` 504,280; `ITEM II.1`
555,601; `ITEM II.1A` 370,327; `ITEM II.2` 484,216; `ITEM II.3` 380,900; `ITEM II.4` 436,579;
`ITEM II.5` 434,114; `ITEM II.6` 712,118; `TOC` 519,117; null 1,057,848.

That is `PART I`, `PART II`, the Part I items `I.1`..`I.4` and the Part II items `II.1`,
`II.1A`, `II.2`..`II.6` (`form10q.PART1_TITLES`, `PART2_TITLES`). `Item 4T` folds into
`ITEM I.4` (`gram.item.4t`). The grammar's provisional `ITEM ?.<k>` marker for an item whose
Part the title does not decide is resolved by position before the row is written
(`gram.part_from_context`); no `?` label appears in the baseline. (The 32 `ITEM 1`, 30
`ITEM 2`, ... rows in the same census are the 850 `form10k`-routed nodes, and the `SECTION`
and `ARTICLE` rows the 142 `contract`-routed ones.)

**`contract`**: `ARTICLE <n>` for n in 1..40 (roman, arabic or spelled numerals all
canonicalise to arabic), `SECTION <a>` (flat numbering) or `SECTION <a>.<bb>[.<cc>]`
(two-digit zero-padded second component: `SECTION 7.01`), and a clause marker as printed
in parentheses: `(a)`, `(i)`, `(A)`, `(I)`, `(1)`, `(aa)` (`contract.ContractGrammar.canonicalize`).
`TOC` on contents regions.

### 7.4 `meta`, `segment`, `path`

Section 6. `meta` in {1, 2, 3} on nodes (0 is defined and unused); `segment` in 0..31;
`path` always 8 entries.

### 7.5 `profile_era`, `profile_publisher`

`profile_era` (`classify.classify`): `text` (plain-text filing), `html_early` (self-filed
HTML built on `<font>`), `html_generic` (self-filed HTML built on `<div>`), `html_publisher`
(a known filing agent's HTML), `ixbrl` (inline XBRL), `image_text` (scanned pages with a
hidden OCR layer; nodes carry `ocr.degraded`). The 10-K baseline holds `html_publisher`
89,738, `text` 60,644, `ixbrl` 35,860, `html_early` 35,305, `html_generic` 7,239 documents
(`r0-contract-vocab2.txt`).

`profile_publisher` is the filing-agent family from `classify.AGENT_FAMILIES` and the
generator fingerprints, with `self` (filer is its own agent) and `other` (an agent not in the
table). The 10-K baseline holds 19 values: `other`, `donnelley`, `self`, `bowne`, `workiva`,
`merrill`, `vintage`, `activedisclosure`, `toppan_bridge`, `m2_compliance`, `rdg`,
`edgar_agents`, `broadridge`, `novaworks`, `compsci`, `newsfile`, `issuer_direct`,
`globalone`, `edgar_online`. This is an open list: a family may be added in a minor version
(it changes no node), so it is descriptive, not promised.

### 7.6 `rejected.kind` and `rejected.reason`

`kind`: `part`, `item`, `heading` (10-K, 10-Q, EX-13); `article`, `section`, `clause`
(contract).

`reason`, with the baseline counts:

| reason | meaning | 10-K | 10-Q | EX-10 | EX-13 |
|---|---|---:|---:|---:|---:|
| `toc` | the candidate is a table-of-contents or index row (a detected region, `rej.vetoed_index_row`, `rej.ctoc_row`, or a condemned member) | 3,579,225 | 5,486,821 | 1,385,659 | 62 |
| `nonmonotone` | the label does not fit the maximum-weight increasing chain at its level | 315,725 | 894,782 | 41,913 | 145 |
| `low_score` | the candidate's score is below the minimum (0.35) | 59,931 | 86,836 | 74,004 | 73 |
| `weak_duplicate` | displaced from its slot by a later, stronger copy of the same label (`seq.strong_duplicate`) | 286 | 2,222 | 0 | 0 |
| `regab_back_matter` | an untitled Regulation AB item at or past the back-matter boundary (`rej.regab_back_matter`) | 272 | 0 | 0 | 0 |
| `rej.page_banner` | a sub-heading that repeats a running page header (`kind` `heading`) | 1,675,227 | 2,453,578 | 0 | 71,247 |
| `clause_nonmonotone` | a clause the sequencer could not place at any cost | 0 | 0 | 36,452 | 0 |
| `clause_outside_section` | a live clause with no accepted section to sit under | 0 | 0 | 70,912 | 0 |
| `section_xref` | a definitional `Section n.n` cross-reference at the head of a line (`rej.section_xref`) | 0 | 0 | 11,868 | 0 |

The 10-Q's one `section` row is a `contract`-routed document.

### 7.7 `ibr_target`, `satisfies_item`

`ibr_target`: `ex13`, `annual_report`, null (10-K baseline: 13,847 / 56,856 / 26,288,082).
`satisfies_item`: `ITEM 6`, `ITEM 7`, `ITEM 8`, null (EX-13 baseline: 7,702 / 9,314 / 62,477 /
739,147; always null on the other corpora).

## 8. Confidence, rule ids and the flag columns

### 8.1 `confidence`

The score the candidate had when it was placed, capped at 1.0 (`tree.py`: `min(1.0, c.score)`).
Scores are additive rule evidence (style, position, title match, penalties for
cross-reference phrasing, leaders, page numbers, prose; `candidates.py`), not probabilities.
Fixed values: the root 1.0; a `toc` node 0.8; a synthetic part, article or section 0.5;
run-in sub-headings 0.55 (`sty.runin`); every non-root node of an `image_text` document is
capped at 0.6 (`ocr.degraded`). Observed range in the baselines: 0.35..1.0 on the 10-K,
0.0..1.0 on EX-10 (`r0-contract-vocab2.txt`). Compare confidences within a level kind, not
across kinds.

### 8.2 `rule_ids`

The ids of every rule that proposed, scored, placed, re-parented or tagged the node, in the
order they fired. Families: `gram.*` (grammar and label matching: `gram.item.title_match`,
`gram.synth_part`, `gram.item.regab`, `gram.form_from_header`, ...), `sty.*` (style evidence),
`toc.*` (contents evidence), `rej.*` (penalties and vetoes that were applied), `seq.*`
(sequencing decisions: `seq.out_of_order`, `seq.strong_duplicate`, `seq.per_segment`,
`seq.regab_run`, ...), `chain.*` and `seg.*` (numbering restarts and instrument boundaries),
`agenda.*` (back-matter and segment placement), `tree.*` (span and parent changes:
`tree.back_truncate_descend`), `kind.*` (EX-13 classification), `multi.ITEM <k>` (the extra
items a combined heading covers), `ocr.degraded`, `doc`.

`rule_ids` is provenance: it says why a row exists, so a reader can enumerate the population
a rule produced. It is an **open vocabulary**: a minor version may add a rule id (every turn
has) and may stop emitting one whose rule was retired. The promise covers the tree the ids
explain, not the id set. The rejected table's `reason` is the closed counterpart (section
7.6); the reason a rejection carries is the last decision, and the tags that led to it are
not written.

The full vocabulary at this version, with the module that writes each id, the module that
reads it and the turn documents that introduced it, is `docs/RULES.md`, generated from the
source by `scripts/rules_catalogue.py`; `docs/ARCHITECTURE.md` shows where each stage sits.

### 8.3 `satisfies_item`

Set only on `heading` nodes of an EX-13 annual report parsed with `--kind ex13`. An annual
report has no `ITEM N` headings; its "Management's Discussion and Analysis", "Consolidated
Balance Sheets", "Selected Financial Data" sections are what a 10-K's Items 7, 8 and 6
incorporate by reference. `ars_kind.classify` maps a heading's title to one of those items
and records the mechanism as a `kind.*` rule id (`kind.mdna`, `kind.finstmt`, `kind.notes`,
`kind.auditors`, `kind.selected`, and their `_alias` forms). A heading inside a detected
contents region is never classified (`pipeline.parse_prepared`).

### 8.4 `covers_items`

A 10-K or 10-Q heading that names several items (`ITEMS 1 AND 2. BUSINESS AND PROPERTIES`)
is one node whose `label_canon` is the first item and whose `rule_ids` carry `multi.ITEM <k>`
for each further item; `covers_items` is those keys read off the ids (`pipeline.covers_items_of`:
`["2"]`, not `["ITEM 2"]`). Null when the node is not a multi-item heading. On the 10-Q the
credit is Part-qualified in `documents.items_found` (`ITEM I.2`), not in `covers_items`.

### 8.5 `item_incorporated_by_reference`, `ibr_target`, `ibr_target_in_submission`, `item_cross_reference`

Computed only for `item` nodes of the `form10k` and `form10q` grammars whose tag-stripped
span is under `_IBR_SPAN_CAP = 4096` characters, i.e. items that are a pointer stub rather
than content (`classify.ibr_flags`, called from `pipeline.parse_prepared`); on every other
node all four are null (23,263,183 of the 10-K's 26,358,785 nodes).

* `item_incorporated_by_reference` is true when the stub says the item is incorporated by
  reference **and** the target is outside the document: an EX-13 exhibit (`ibr_target`
  `ex13`) or the annual report to shareholders (`ibr_target` `annual_report`).
* `ibr_target_in_submission` is set only when that flag is true: whether an `EX-13*`
  `<DOCUMENT>` exists in the same submission (10-K baseline: 57,588 true, 13,115 false).
* `item_cross_reference` is true when the flag is false and the stub instead points inside
  the document ("see Item 8", "incorporated herein by reference to Note 12"); 951,489 of
  the 10-K's flagged stubs. The two booleans are never both true.

## 9. The promise

The version stamped in `parser_version` and `normalizer_version` is the package version
(`docs/VERSIONING.md`). Within a major version:

* no column is added, removed, renamed or retyped in any of the three tables, except by
  appending a column at the end of a table in a minor version (as 1.0.0 does with
  `input_sha256`, `input_bytes` and `manifest_cik`); existing columns keep their meaning
  (the one pre-release meaning change, `cik` from the header rather than the manifest, is
  made at 1.0.0 itself, decision D9);
* the closed vocabularies of sections 7.1-7.4, 7.6 and 7.7 (`grammar`, `level_kind`,
  `label_canon`, `meta`, `segment`, `rejected.kind`, `rejected.reason`, `ibr_target`,
  `satisfies_item`) and `profile_era` do not change: no label is spelled differently, no
  kind is renamed, no reason is added or removed;
* the semantics of sections 5, 6 and 8 do not change: what a `raw_start` points into, what a
  span covers, what a path digit means, what the flags mean.

Across versions:

* a **minor** version may add, remove or move nodes (a tree-behaviour change: a new rule, an
  amended one, a retired one) and may add or retire `rule_ids`, and its `CHANGELOG.md` entry
  names the gate that measured the change on every corpus;
* a **patch** version changes no output row: on the same input files it produces the same
  `nodes` and `rejected` rows and the same `documents` rows apart from the version stamp;
* a **major** version is the only place a column, a closed vocabulary or an offset, path or
  span semantic changes.

Not covered: `documents.signals` and `level_profile` (diagnostic JSON), `norm_start`/`norm_end`
and `normalized_text` (normaliser internals), `profile_publisher` (an open list of agent
families), the `rule_ids` vocabulary (provenance, open), and the viewer, judge and evaluation
tooling. A released version is never modified; a wrong output in a released version is
recorded in `ERRATA.md` and fixed in the next minor.
