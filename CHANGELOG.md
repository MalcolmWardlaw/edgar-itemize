# Changelog

All notable changes to edgar-itemize. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
the version policy is `docs/VERSIONING.md`. Every number below comes from the turn report or
gate artifact named beside it. Rule ids are the `rule_ids` / `rejected.reason` values the
change writes (`docs/OUTPUT_CONTRACT.md` section 8.2).

A `runs/...` name beside a number is the lab's identifier for the stored artifact the number
was read from; the artifacts are not in this repository, and the curated evaluation record
that resolves them ships with release 1.0.1 (`docs/RELEASE_PLAN.md` decision D3).
`runs/text_vN` are the runs on a private loan-contracts corpus of raw exhibit text
(`--kind text`; not published).

## [1.0.0] - 2026-10-08

The first numbered release. Tree behaviour is that of the Turn 13 close (`runs/full_v30`,
`runs/full_10q_v11`, `runs/full_ex10_v24`, `runs/full_ex13_v12`, `runs/text_v8`); the
release's reference re-parse is gated as an identity against those runs on `nodes` and
`rejected`, with `documents` differing only in the version stamp and the two new columns
(`docs/RELEASE_PLAN.md` section 5 item 3). The R0 gate on EX-10 is `runs/diff_r0_ex10.log`.

The `v1.0.0` tag is at public commit f95bc58 (lab 672e63c). That commit carries this section
headed `unreleased` and a `CITATION.cff` without `date-released` or `doi`; both were filled in
by the next commit on `main`, after the Zenodo mint (`docs/release_decisions/r2_tag.md`). Tags
are never moved, so the sdist on PyPI and the Zenodo archive keep the undated files.

### Changed
- Renamed from `edgar-agenda` to **`edgar-itemize`**: package `edgar_itemize`, CLI
  `edgar-itemize`, environment variables `EDGAR_ITEMIZE_*` (`EDGAR_ITEMIZE_DATA_ROOT`).
- The version is single-sourced from package metadata. `parser_version` and
  `normalizer_version` now both stamp the package version; they had been the hand-kept
  constants `0.4.0` / `0.2.0` since Turn 5 (`docs/RELEASE_PLAN.md` section 1 item 2).
- Dependencies split into extras: core (`regex`, `pyarrow`, `pyyaml`), `[viewer]`
  (fastapi, uvicorn), `[judge]` (httpx), `[dev]` (pytest, polars). The deterministic claim
  covers the core.
- No private defaults: `EDGAR_ITEMIZE_DATA_ROOT` must be set (the CLI says how), and the
  judge client requires its server URL from the environment.
- `cik` (in `nodes` and `documents`) is now read from the submission's own SGML header, the
  first FILER's `CENTRAL INDEX KEY`, unpadded (`sgml.header_cik`), rather than taken from
  the manifest; it is therefore a function of the input bytes, and a co-registrant filing
  listed under several CIK directories gets the same `cik` from every copy. Headerless
  input (`--kind text`) and error rows keep the manifest's value. Decision D9
  (`docs/RELEASE_PLAN.md` section 9: the header CIK is present in 13,168/13,168 2019-2020
  10-K files and differs from the manifest's on 182 of them, 1.4%). Gate on EX-10:
  `runs/diff_r1c_ex10.log` (identity on `nodes` and `rejected` with `cik` excluded) and
  `runs/r1c_cik_classes.txt` (the `cik`-changed rows counted as their own class).
- The HTML normaliser tokenises with a vendored copy of CPython 3.11.15's `html.parser`
  (`src/edgar_itemize/_html_parser.py`, PSF-2.0, `LICENSES/PSF-2.0.txt`) instead of the
  running interpreter's. The R1.4 Python matrix found the stock parser drifting between
  CPython patch releases on malformed markup: the conformance set was identical under
  3.11.15 and 3.12.13 (`runs/r1_py3.11_verify.json`, `runs/r1_py3.12_verify.json`) but
  3 of 2,001 documents hashed differently inside the container's 3.12.14 (10-K
  `0000073887-10-000041` seq 1, EX-10 `0000054681-07-000032` seq 2, EX-13
  `0000004904-11-000026` seq 6; `runs/r1_docker_verify.txt`) and 1 under 3.13.12 and
  3.14.3 (EX-13 `0000317771-02-000037` seq 5; `runs/r1_py3.13_verify.json`). Normalised
  block counts under the vendored parser against the stock ones (`runs/r1e_blocks.txt`):
  10,294 -> 4,867, 1,961 -> 236 and 39,519 -> 12,628 under 3.12.14; 2,775 -> 1,449 under
  3.13.12 and 3.14.3. The constructs (`runs/r1e_snippets.txt`): a numeric
  character reference split by a line wrap (`&#` + newline + `160;`), which 3.12.14 meets
  at end of input because an empty `feed("")` no longer re-enters the tokenizer, and a
  reference run into a hex-letter word without `;` (`&#147Change`), which 3.13.12+ turn
  into one character reference spanning the rest of the document; in both cases the text
  after the construct was swallowed. The 3.11.15 parser produced every baseline and gate
  of record, so its behaviour is the behaviour of record on every interpreter. Its
  dependencies (`html.unescape`, `html.entities`, `_markupbase`) are identical or differ
  only in comments across 3.11.15-3.14.3 (`runs/r1e_stdlib_diff.txt`) and stay stdlib.
  Regression test `tests/test_html_parser_vendored.py` (event streams recorded from the
  vendored parser, synthetic snippets plus excerpts of the two real filings); gate
  `runs/r1e_matrix.txt` (the conformance set under 3.11-3.14 and the rebuilt container),
  `runs/diff_r1e_ex10.log` and `runs/diff_r1e_ex13.log` (full-corpus identity against
  `full_ex10_r0` and `full_ex13_v12`).

### Fixed
- The conformance hashing pool (`verify`, `conformance draw`, `conformance.hash_rows`)
  starts its workers with an explicit `forkserver` context (`spawn` where the platform has
  no forkserver) and passes the data root in each task instead of relying on inherited
  module globals, so it behaves the same on CPython 3.11-3.14 (whose Linux default moved
  from `fork` to `forkserver` in 3.14). The R1.4 matrix's per-document hashing pass had
  crashed under 3.14.3 (`ConnectionResetError` in the forkserver, `runs/r1_python_matrix.log`)
  because it ran from a `python -` heredoc, whose `<stdin>` main no spawn-based start method
  can import; the pass is now the file script `scripts/r1/hash_conformance.py` and completes
  under 3.14.3 with 2001/2001 hashes equal to the set (`runs/r1e_py3.14_hash_compare.txt`).
  `scripts/r1/python_matrix.sh` also no longer aborts before its comparison when one
  version fails, and includes the container run.

### Added
- `edgar-itemize manifest`: builds a parse manifest from the SEC's public quarterly full
  index (`full-index/<year>/QTR<n>/master.idx`, cached under `<data-root>/full-index`),
  with no dependence on the control tables; same columns and types as the manifests of
  record. Gate on the 10-K family, filing years 2019-2020: the public and control-table
  manifests select the same 13,168 accessions (0 only-public, 0 only-control; the 41 10-KT
  rows the index adds are absent from the mirror and dropped by `--only-present`; 1,897 `/A`
  rows dropped; 263 co-registrant filings carry a different CIK stamp, the files being
  byte-identical under both CIK directories) (`runs/r1_manifest_compare.txt`).
- `edgar-itemize fetch`: downloads the full-submission files a manifest names into the
  mirror layout under the SEC's fair-access rules (declared `User-Agent`, 10 requests per
  second, backoff on 429/503), resumable, atomic writes, SHA-256 of every saved file in
  `<data-root>/fetch_log.jsonl`. `--dry-run` counts without the network.
- `documents.input_sha256` and `documents.input_bytes`: the SHA-256 and size of the raw
  submission file each document was read from, the unit the reproducibility promise is
  stated over (`docs/VERSIONING.md` section 4).
- `documents.manifest_cik` (column 28, after `input_bytes`): the CIK the manifest listed
  for the row, i.e. the mirror directory the file was read from, so the difference from the
  header-derived `cik` is visible and joins to the mirror layout still work (D9;
  `runs/r1c_cik_classes.txt` counts the rows where the two differ on EX-10).
- `LICENSE` (MIT), `CITATION.cff`, `CONTRIBUTING.md`, `ERRATA.md`, this changelog,
  `docs/OUTPUT_CONTRACT.md` and `docs/VERSIONING.md`.
- `edgar-itemize verify [--data-root DIR] [--set DIR] [--workers N] [--json]`: re-parses the
  release's conformance set offline, checks every input hash (`input differs` / `input
  missing` reported separately), then compares the canonical per-document output hashes
  (`docs/VERSIONING.md` section 6); exit code is the verdict.
- `edgar-itemize conformance draw`: the deterministic draw of the conformance set
  (`conformance/<version>/manifest.parquet`, `expected.parquet`, `README.md`): 2,000
  documents stratified by `profile_era` and year across the 10-K, 10-Q, EX-10 and EX-13
  baselines, keyed-hash bottom-k with a fixed seed, plus every `tests/data/` fixture.
- `edgar-itemize conformance hashes --run DIR --kind K --out FILE.parquet`: the
  per-document hash manifest (`accession_number, sequence, input_sha256, input_bytes,
  output_sha256, n_nodes, n_rejected`) of a full run, published per corpus with each release.
- `parse` accepts a manifest whose `archive_path` is relative to the data root
  (`archives/edgar/data/<cik>/<accession>.txt`), which is what the conformance set carries.
- Container image (`Dockerfile`, multi-stage from a digest-pinned `python:3.12-slim`, `uv`
  pinned by tag and digest, `uv sync --frozen --no-dev`, core package only, non-root, the
  conformance set and fixtures inside so `docker run ... verify --data-root /data` checks the
  image against a mounted mirror), published as `ghcr.io/malcolmwardlaw/edgar-itemize:<tag>`;
  and GitHub Actions CI (`.github/workflows/ci.yml`: tests on CPython 3.11-3.14 from the
  lockfile plus `verify` on the fixture subset of the conformance set with an empty data
  root; `release.yml`: on a `v*` tag, sdist and wheel to PyPI through trusted publishing, the
  image to ghcr.io, `conformance/<version>/` attached to the GitHub release).
- `docs/ARCHITECTURE.md`: the parser's flow as diagrams (context, one document through the
  stages, grammar routing, the two tree builders, a candidate's life, paths and the back
  matter, a run, the objects, where to look); and `docs/RULES.md`, the catalogue of every
  rule id and rejected reason with the module that writes it, the module that reads it and
  the turn documents that name it, generated from the source by
  `scripts/rules_catalogue.py` and kept current by `tests/test_rules_catalogue.py`.
  `scripts/render_diagrams.py` renders every diagram of both files to SVG under
  `docs/diagrams/` with mermaid-cli in Docker (`minlag/mermaid-cli`), which is also the
  syntax check for the Mermaid sources.
- The manual (`manual/`, `mkdocs.yml`; `pip install 'edgar-itemize[docs]'` or
  `uv sync --extra docs`, `uv run mkdocs serve`): the curated public documentation, built
  with MkDocs and deployed to GitHub Pages (`.github/workflows/docs.yml`). Overview,
  install, quickstart, concepts, how the parser works (the diagrams, rendered live),
  command line, reproducibility, evaluation, citing; the output contract, the versioning
  policy, the rule catalogue, the related-parsers survey, the changelog, contributing and
  errata are included from their single source at build time.

## Pre-release turns

Before 1.0.0 the parser was developed in numbered turns without version tags. Each turn
closed with a report (`docs/TURN<n>_REPORT.md`) whose "what changed" section and corpus gates
are summarised here. The version stamp in every run from these turns is `0.4.0` / `0.2.0`.

### Turn 13 (2026-09-28)

`docs/TURN13_REPORT.md`. Baselines at close: `runs/full_v30`, `runs/full_10q_v11`,
`runs/full_ex10_v24`, `runs/full_ex13_v12`, `runs/text_v8`.

- Contract: `rej.section_xref` rejects a definitional `Section n.n` cross-reference at the
  head of a line before the section chain (new `rejected.reason` `section_xref`, 11,868 EX-10
  rows); `gram.synth_section_xref` keeps the clauses a vetoed reference orphans under a
  synthetic section (4 treeless documents against Turn 11's 79). Bank 100/100
  (`runs/judge/turn13-b4-consensus-report.txt`); gate EX-10 v23 -> v24 sections +248 / -570,
  articles +48 / -90 (`runs/diff_ex10_v23_v24`).
- 10-K: Regulation AB items 1112, 1114, 1115, 1117, 1119, 1122, 1123 are matched
  (`gram.item.regab`), chained as their own run (`seq.regab_run`) under a synthetic
  `PART V`, with `rej.regab_back_matter` vetoing untitled Reg-AB candidates past the
  back-matter boundary: +79,469 Reg-AB item nodes, +11,962 `PART V`, 0 Item 15/16 displaced
  (`runs/diff_v29_v30`, items +79,470 / -3).
- 10-K / 10-Q: the Item 15 and 16 statutory titles widened (singular "Exhibit and ...", the
  en-dash "10–K Summary"); a 10-Q suffix-refused line's title re-matched on the raw line.
  10-K +1 / -0, 10-Q +115 / -4 (`runs/diff_10q_v10_v11`).
- Infrastructure: a per-document normalisation cache (`parse --norm-cache DIR
  --norm-cache-mode {off,read,write,readwrite}`, off by default).
- Measured, not merged: the external comparators (edgartools, datamule, EDGAR-CORPUS,
  LexNLP, sec-parser, doc2dict) on five judged banks; B.5 (K1 narrowings) failed both gates
  and is deferred. Audit re-score on the same 1,000 windows: accepted precision 94.2% ->
  94.4%, top-level 97.4% -> 98.2%, recall gap 11.2% -> 11.0%
  (`runs/judge/turn13-audit-rescore.txt`).

### Turn 12 (2026-09-24)

`docs/TURN12_REPORT.md`. Baselines at close: `runs/full_v29`, `runs/full_10q_v10`,
`runs/full_ex10_v23`, `runs/full_ex13_v11`, `runs/text_v7`.

- `tree.back_truncate_descend`: a sub-heading whose parent was cut at the back-matter
  boundary is re-parented to the nearest containing ancestor with every span unchanged:
  1,147,952 10-K headings (`runs/diff_v25_v26.log`, `raw_end changed 0`).
- `rej.vetoed_index_row`: a member of an ordered index run with a later title-matched copy
  is rejected as `toc`: 45,048 10-K and 264,554 10-Q rows condemned, 1,209 and 5,694 anchors
  moved onto the body heading, 662 and 4,009 labels lost, of which 657 and 3,996 had no body
  candidate (`runs/judge/turn12-b1-10k-losses.txt`, `turn12-b1-10q-losses.txt`); the
  contract leader arm condemns 10,581 dot-leader index rows at 122/122 bank precision.
- Contract clause layer: `seq.open_midrun`, `gram.clause_under_article`,
  `gram.synth_section`, `gram.clause_alpha_bijective`, `seq.alt_readings`, and every
  dropped clause candidate written out as `clause_outside_section`: +142,988 accepted clause
  nodes net on the two contract corpora (`runs/judge/turn12-b2-gate-read.txt`).
- `rej.page_banner`: a sub-heading repeating a running page header at a page break is
  dropped, with a rejected row: 1,675,210 10-K and 71,247 EX-13 copies
  (`runs/judge/turn12-b3-losses-10k.txt`).
- 10-Q label family: roman and spelled item numbers and the dot/dash/paren suffix separator
  (`gram.item.roman`, `gram.item.word`, `gram.item.suffix_sep`), the Part decided from the
  statutory title with the substring hints as second tier (`gram.item.part_hint`),
  `gram.item.suffix_refused`, Part-qualified multi-item credit (`seq.out_of_order_multi`):
  +10,692 items against 856 lost (`runs/judge/turn12-b4-10q-v9-losses.txt`). The Reg-AB
  level was withdrawn from this turn after displacing 1,279 Item 15/16 headings.
- EX-13: a guarded back-matter boundary (33 documents, 183 nodes) and manifest v2
  (`runs/full_manifest_ex13_v2.parquet`: 161 uuencoded binary payloads dropped,
  `contains_other_form` flag).
- Whole turn: 10-K v25 -> v29 items +1 / -662, nodes 27,942,644 -> 26,267,200
  (`runs/diff_v25_v29`); 10-Q v6 -> v10 items +10,557 / -4,717 (`runs/diff_10q_v6_v10`);
  five-corpus audit precision 91.8% -> 94.2%, recall gap 15.4% -> 11.2%
  (`runs/judge/turn12-audit-rescore.txt`).

### Turn 11 (2026-09-22)

`docs/TURN11_REPORT.md`. Baselines at close: `runs/full_v25`, `runs/full_10q_v6`,
`runs/full_ex10_v20`, `runs/full_ex13_v8`, `runs/text_v4`.

- Contract table-of-contents boundary, both sides: `rej.ctoc_row` (with `ctoc.row_cell`,
  `ctoc.row_gap`, `ctoc.row_interleave`, `ctoc.row_wrap`) condemns two-column index rows
  whose second column is a page pointer, rejected as `toc`; `ctoc.region_relief` spares a
  real body heading a detected region had swallowed.
- Gate EX-10 v19 -> v20: sections +162 / -16,317, articles +17 / -511, anchors moved earlier
  10,112 (`runs/diff_ex10_v19_v20`); contracts-text v3 -> v4 sections +300 / -4,929. 134 of the 137
  hand-confirmed false accepts condemned, 0 re-admitted
  (`runs/judge/turn11-b12-acceptance.txt`); condemnation re-earned out of sample at 1.000 on
  100 windows (`runs/judge/turn11-ctoc-b1v2-bank-report.txt`).
- 10-K, 10-Q and EX-13: exact identity, 0 documents changed (`runs/diff_v24_v25`,
  `runs/diff_10q_v5_v6`, `runs/diff_ex13_v7_v8`).
- The 101 carried parse errors classified: 98 header-only submissions, 2 truncated files,
  1 missing mirror file, no parser defect (`docs/turn11_decisions/a6_parse_errors.md`).

### Turn 10 (2026-09-18)

`docs/TURN10_REPORT.md`. Baselines at close: `runs/full_v24`, `runs/full_10q_v5`,
`runs/full_ex10_v19`, `runs/full_ex13_v7`, `runs/text_v3`.

- 10-Q: a statutory title table for `Form10QGrammar`, making `gram.item.title_match` and
  `seq.out_of_order` reachable on the 10-Q. Gate 10-Q v3 -> v5 items +5,547 / -15, parts
  +59 / -0 (`runs/diff_10q_v3_v5`).
- 10-K: `rej.fm_table` (`fm.row_cell`, `fm.row_gap`, `fm.row_interleave`) blocks the rows of
  a two-column front-matter table from out-of-order placement: items +0 / -73, every loss a
  table row (`runs/diff_v22_v24`).
- `seq.strong_duplicate_backmatter`: a repeated-Part guard on the strong-duplicate
  promotion; its back-matter-marker half was removed after the corpus gate found it 0 of 7
  right (`runs/judge/turn10-b0-regression.txt`).
- Contract: `gram.title_adjacent_line` and `gram.title_above_label` attach a bare title line
  to a titleless article or section: 75,336 EX-10 and 93,086 contracts-text node titles, 0 path
  churn, 0 moved `head_raw_start` (`runs/diff_ex10_v17_v19`, `runs/diff_text_v1b_v3`).
- Evaluation only: the judge protocol became sonnet-first (`scripts/judge_consensus.py
  --protocol`).

### Turn 9 (2026-09-16)

`docs/TURN9_REPORT.md` (addendum section 7). Baselines at close: `runs/full_v22`,
`runs/full_10q_v3`, `runs/full_ex10_v17`, `runs/full_ex13_v5`, `runs/text_v1`.

- `--kind text`: a raw `.txt` contract is parsed as a one-document submission with
  offsets into the file (`sgml.load_text_submission`).
- Contract: `chain.section_restart` opens a new sequencing chain where section numbering
  goes back to the top (13,715 restarts in 7,608 of 17,371 documents), and
  `rej.formula_denominator` stops a `1.00 - ...` fraction line proposing `SECTION 1.00`.
  Gate EX-10 v15 -> v17 sections +32,282 / -355, articles +969 / -28
  (`runs/diff_ex10_v15_v16b`, `runs/diff_ex10_v16b_v17`).
- 10-K: the cover-page caption guard `rej.caption_omission`, `rej.caption_ibr`,
  `rej.caption_xref` bars an omission-list entry from out-of-order placement: items
  +0 / -36 (`runs/diff_v19_v20`).
- `seq.strong_duplicate` / `seq.weak_duplicate` (new `rejected.reason` `weak_duplicate`): a
  later, stronger copy of a placed label takes its slot. 10-Q: 6,207 anchors moved off
  contents rows onto body headings (`runs/diff_10q_v1_v2`), 281 backwards moves reverted by
  the later-only guard (`runs/diff_10q_v2_v3`); widened to the 10-K in the addendum, 839
  anchor moves in 281 filings, labels +0 / -0 (`runs/diff_v21_v22`).
- First full 10-Q corpus run (`runs/full_10q_v3`, 736,835 filings).

### Turn 8 (2026-09-14)

`docs/TURN8_REPORT.md`. Baselines at close: `runs/full_v19`, `runs/full_ex10_v15`,
`runs/full_ex13_v4`, `runs/sample_10q_v3`.

- Contract: per-chain article and section sequencing (`agenda.chain_restarts`,
  `seq.per_segment`), so more than one `ARTICLE 1` can exist per document; numbering
  restarts (`chain.restart`, `chain.restart_unsegmented`) decoupled from instrument
  boundaries (`seg.instrument_boundary`, `seg.prior_closed`, `seg.new_title`,
  `seg.new_parties`), which alone drive the `segment` column. Gate EX-10 v11 -> v15
  sections +12,332 / -266, articles +385 / -19, multi-segment documents 117 -> 197
  (`runs/diff_ex10_v11_v15.json`).
- `agenda.back_iww_before_tail`: an execution clause no article follows opens its segment's
  back matter (239/274 judged IWW windows in back matter, `runs/judge/turn8-iww-b3-gate1-v2.jsonl`).
- 10-K: `seq.out_of_order` places a title-matched item whose label the chain left unclaimed,
  under the vetoed-index guard `toc.vetoed_index`: items +4,783 / -0, parts +20 / -0,
  core_complete 222,285 -> 222,451 (`runs/diff_v17_v18b.json`, `runs/diff_v17_v19.json`).
- EX-13: `kind.*_alias` patterns and a TOC-row guard in the `satisfies_item` classifier
  (ITEM 6 4,142 -> 7,745 headings, ITEM 7 13,861 -> 14,554, ITEM 8 80,644 -> 82,594).
- 10-Q: `gram.item.4t` (the 2007-2010 "Item 4T") and `gram.synth_item1` (a synthetic Part I
  Item 1 at the financial-statement caption): I.1 presence 0.889 -> 0.927 on the 20,000-filing
  sample.

### Turn 7 (2026-09-13)

`docs/TURN7_REPORT.md`. Baselines at close: `runs/full_v17`, `runs/full_ex10_v11`,
`runs/full_ex13_v2`.

- Back matter: `agenda.back_after_sigs`, `agenda.back_attestation`, `agenda.back_head_strict`,
  `agenda.back_iww`, and `tree.back_truncate`, which cuts a main-body span at the boundary
  so the last item no longer runs through the financial appendix (last-item `raw_end`
  shortened in 224,657 filings, `runs/diff_v10_v17.json`).
- EX-13 annual reports parsed as documents (`parse --kind ex13`): headings classified by
  `ars_kind.py` into the new `satisfies_item` column with `kind.mdna`, `kind.finstmt`,
  `kind.notes`, `kind.auditors`, `kind.selected`.
- Compound exhibits: `agenda.compound_restart` and the new `segment` column
  (`documents.n_segments`), each glued agreement with its own ordinals and back matter.
- New `covers_items` column from the `multi.ITEM <k>` rule ids; the 10-Q expected-items
  tiers (REQUIRED / ERA_REQUIRED / OMITTABLE).
- Contract TOC condemnation without leaders (`toc.section_run`, `toc.dup_later_contract`).
- Turn 4 grammar recall: `gram.item.word`, `gram.item.multi_repeat`, `gram.item.title_match`,
  `gram.form_from_header`, the later-line scan. Whole turn 10-K v10 -> v17 items
  +2,753 / -119, parts +94 / -0, core_complete 221,780 -> 222,285 (`runs/diff_v10_v17.json`).

### Turn 6 (2026-09-08)

`docs/TURN6_REPORT.md`. Baseline at close: `runs/full_ex10_v6`.

- Clause sequencer: the restart move (`seq.restart`, cost 0.35) lets a `(a)` or `(i)` close
  an open level and start a sibling list; same-family descent costs 0.5.
- `IN WITNESS WHEREOF` opens the back matter regardless of how much follows; the 60-character
  gate for other markers applies to the block's first line.
- Scanned exhibits (`image_text`): the hidden OCR layer is split into pseudo-lines and
  scanned for labels; resulting nodes carry `ocr.degraded` with confidence capped at 0.6.
- Gate EX-10 v3 -> v6: sections +21,775 / -14, articles +2,975 / -1, documents with >= 10
  sections 15,689 -> 15,889 (`runs/diff_ex10_v3_v6.json`).

### Turn 5 (2026-09-08)

`docs/TURN5_REPORT.md`. Baseline at close: `runs/full_v10`. Parser stamp `0.4.0`,
normalizer `0.2.0`.

- Run-in sub-headings: a bold or underlined lead-in closed by a period or colon and
  followed by prose becomes a `heading` node (`sty.runin`, confidence 0.55): 1,598,875
  such nodes on the 10-K.
- Page-furniture suppression: a `(continued)`-marked block is never a sub-heading;
  `rej.continued_first`, `rej.continued_after_copy` and `rej.page_repeat` keep a
  continuation or page-header copy of an item label from anchoring the item
  (continuation-titled nodes 1,418,646 -> 762).
- Normaliser: `Table of Contents` / `Back to top` back-links glued to a block are dropped
  from its text and span.
- Gate 10-K v7 -> v10: item labels +25 / -0, core_complete 219,015 -> 219,025, 3,816 item
  anchors moved earlier and 0 later (`runs/diff_v7_v10.json`).
