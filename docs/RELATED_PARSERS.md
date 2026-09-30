# Related EDGAR structure parsers (survey, 2026-09-26)

Written after Turn 12 to answer one question: how much of edgar-agenda duplicates
existing work. Facts below come from each project's README, PyPI page or paper as read on
2026-09-26; stars and last-push dates from the GitHub API the same day.

## The field

| project | what it does | input | forms | structure | accuracy published | status |
|---|---|---|---|---|---|---|
| [edgar-crawler](https://github.com/lefterisloukas/edgar-crawler) (Loukas, AUEB; WWW 2025) | downloads filings, splits items into JSON | HTML-stripped text, regex on item headers | 10-K items 1-16 incl. 1A/1B/1C/9A-9C, 10-Q parts and items, 8-K 1.01-9.01 | flat: one string per item; old 10-Qs fall back to whole-part text | none | 546 stars, GPL-3.0, last push 2025-07-18 |
| [EDGAR-CORPUS](https://arxiv.org/abs/2109.14394) (same group, 2021) | pre-split 10-K items 1993-2020 as JSON | same pipeline: "html-stripped, cleaned and split into the different items by using regular expressions" | 10-K | flat items | none for the split itself; the paper evaluates downstream embeddings | dataset on HuggingFace |
| [edgartools](https://github.com/dgunning/edgartools) | general EDGAR client; XBRL, ownership forms, section extraction for 10-K/10-Q | HTML | 20+ form types | items as attributes (Item 1A, Item 7) | none | 2,750 stars, MIT, active daily |
| [sec-parser](https://github.com/alphanome-ai/sec-parser) (alphanome) | HTML to semantic tree: titles, paragraphs, tables under Part/Item | HTML only | 10-Q shown; 10-K/8-K/S-1 mentioned | tree of elements; mechanism undocumented | none | 294 stars, MIT, README says "no longer maintained" |
| [sec-parsers](https://pypi.org/project/sec-parsers/) then [datamule](https://github.com/john-friedman/datamule-python) + [doc2dict](https://github.com/john-friedman/doc2dict) (Friedman) | header detection from tags, CSS, emphasis and position, then a nested dict; mapping dicts say Item nests under Part | HTML, XML, PDF | 10-K, 10-Q, 8-K, S-1, 20-F | nested dict, sub-headers where styling exposes them | none | sec-parsers last release 0.549, 2024-07-29, "WIP"; datamule 557 stars, active |
| [sec2md](https://pypi.org/project/sec2md/) | HTML to Markdown with PART/ITEM boundaries, page-aware, XBRL tags kept; aimed at RAG | HTML only | 10-K 18 items, 10-Q 11, 8-K 41, 20-F, SC 13D/G | items as Markdown sections | none | 0.1.23, 2026-03-15, MIT |
| [sec-api.io Extractor](https://github.com/janlukasschroeder/sec-api-python) | items by name over an API | their pipeline | 10-K, 10-Q, 8-K and more | flat items | none public | commercial |
| [Loughran-McDonald Stage One 10-X](https://sraf.nd.edu/sec-edgar-data/cleaned-10x-files/10x-stage-one-parsing-documentation/) | strips HTML, PDFs and images from every 10-K/10-Q variant 1993-2024 | raw submissions | 10-X | none: a cleaned document, no items | n/a | data files, refreshed |
| [LEDGAR](https://aclanthology.org/2020.lrec-1.155/) (Tuggener et al., LREC 2020) | provisions from EX-10 material contracts labelled by heading, ~100 labels | HTML exhibits | EX-10 | provision segments, semi-automatic, with noise the paper discusses | classification benchmarks only | dataset |
| smaller: [edgar-parser](https://github.com/henrysouchien/edgar-parser), [sec-edgar-toolkit](https://github.com/stefanoamorelli/sec-edgar-toolkit), [ETL-SEC-EDGAR-10-k-Filings](https://github.com/pChitral/ETL-SEC-EDGAR-10-k-Filings) | item or MD&A extraction from HTML | HTML | 10-K mainly | flat | none | 2 to 38 stars |

Two papers measure item segmentation itself:

- [Utilizing Pre-trained and Large Language Models for 10-K Items Segmentation](https://arxiv.org/abs/2502.08875)
  (2025): 3,737 annotated 10-Ks, core items 1, 1A, 3 and 7. Macro-F1: rule-based 0.9048,
  CRF 0.9818, BERT plus Bi-LSTM 0.9825, GPT-4o with line ids 0.9567.
- [From Rules to Flexibility](https://dl.acm.org/doi/10.1145/3746252.3761609) (CIKM 2025):
  premise that rule-based extraction has degraded on post-2021 10-K layouts; proposes fuzzy
  matching plus structural heuristics. Paywalled; numbers not read.

## Second sweep (same day): what the first pass missed

| project | what it adds | status |
|---|---|---|
| [LexNLP](https://github.com/LexPredict/lexpredict-lexnlp) (LexPredict / ContraxSuite) | the one real precedent for the contract grammar: `get_section_spans` returns `DocumentSection` objects with `start`, `end`, `title_start`, `title_end` character offsets and a `level` / `abs_level`; detection is a scikit-learn line classifier (`section_segmenter.pickle`; line length, character classes, case, keywords "section" / "article", a window of neighbouring lines) with a regex fallback `get_sections_re`; `SectionLevelParser` has nine hierarchy levels (appendix / exhibit / schedule / part / title, subtitle, section, subsection, article, upper-case letters, dotted numerals, letters, parentheticals). Output is a flat list with levels, not a tree. Trained and unit-tested on EDGAR documents. | 795 stars, AGPL-3.0, last push 2024-05-27, release 2.3.0 (2023) |
| [SEC-EDGAR-text](https://github.com/alions7000/SEC-EDGAR-text) | the only tool claiming text-era support: "Compatible with all main EDGAR document formats from 1993 onwards"; key sections of 10-K and 10-Q by configurable search terms; the README's own verdict: "Generally accurate in extracting text, but lots of room for improvement". About a million excerpt files. | 111 stars, GPL-3.0, last push 2026-02-01 |
| [edgar (R, CRAN)](https://cran.r-project.org/package=edgar) (Lonare; SoftwareX 2021) | `getBusinDescr` (Item 1) and `getMgmtDisc` (Item 7) only, for 10-K, 10-K405, 10KSB, 10KSB40; regex over text; widely used in finance papers | 31 stars on the mirror, last push 2024-04-03 |
| [pipeline-sec-filings](https://github.com/Unstructured-IO/pipeline-sec-filings) (Unstructured) | section extraction for 10-K, 10-Q, S-1 from iXBRL by predefined section names or custom regex | archived 2024-05-22 |
| [pyedgar](https://github.com/gaulinmp/pyedgar), [py-sec-edgar](https://github.com/ryansmccoy/py-sec-edgar) | download and index only; pyedgar splits `<DOCUMENT>` boundaries of the raw `.nc` submission and stops | 43 and 128 stars |
| [sec-api Form 10-K dataset](https://sec-api.io/datasets/form-10k-content) | 305,479 10-K variants, 1993 to present, 34 GB; documents "as accepted by EDGAR"; no item split, no accuracy statement; pre-2001 described as "Plain-text ASCII wrapped in SGML" | commercial |
| [PleIAs/SEC](https://huggingface.co/datasets/PleIAs/SEC) | EDGAR-CORPUS extended to 2024, the 2021 to 2024 part "collected using the EDGAR-Crawler toolkit"; no validation described | CC0 dataset |
| [AlphaCreek write-up](https://www.alphacreek.ai/blog/how-to-parse-sec-10k-filings-into-sections) | describes the style-based recipe sec-parser and sec2md embody: each `<body>` child a block, PART/ITEM by anchored patterns "prefer the body hit over the table of contents", sub-headings from inline CSS weight and colour, blocks nested under the nearest preceding title of equal or higher rank; one filing as the example, no evaluation | blog, 2026 |
| [Utilizing Pre-trained and Large Language Models for 10-K Items Segmentation](https://arxiv.org/abs/2502.08875) | the only measured comparison found: 3,737 annotated 10-Ks, items 1, 1A, 3, 7; macro-F1 rules 0.9048, CRF 0.9818, BERT 0.9825, GPT-4o 0.9567 | paper, 2025 |

Also seen and not relevant to structure: HuggingFace mirrors of EDGAR-CORPUS
(`khaihernlow/financial-reports-sec`, sentence-split; `eloukas/edgar-corpus`), riskroll
section datasets, KPI-EDGAR, BUSTER, BeanCounter (all downstream NLP corpora), OpenEDGAR
(ingestion, LexPredict 2018), Apify scrapers.

Net of the second sweep: LexNLP is the one thing that should have been in the first table,
because it is the only other project that returns offsets and a hierarchy, and it did so for
contracts. Its flat-list-with-levels output, its 2024 last push and its AGPL licence keep it
a comparator rather than a component. Everything else confirms the first pass. The Turn 13
plan (docs/TURN13_PLAN.md) uses edgartools, datamule, EDGAR-CORPUS, LexNLP, sec-parser and
doc2dict as caged comparators and harvests the regexes of the rest.

## Where edgar-agenda overlaps

The top-level split of a 2001-onward HTML 10-K or 10-Q into Items is done by at least six
open tools and one commercial API. If the deliverable were "Item 7 text for modern 10-Ks",
this project would be duplicating them, and EDGAR-CORPUS would already cover 1993-2020.

## Where it does not

None of the projects above does any of the following, and most do none of them:

1. **Byte offsets into the raw submission** as the coordinate system, with `rule_ids` on
   every node and every rejected candidate written out with its reason. Every tool above
   returns text, not addresses; none exposes what it rejected.
2. **One grammar across the three eras.** Every HTML-based tool degrades by construction
   on 1993-2000 plain text and SGML, which is the part of the panel where a finance paper's
   long sample lives. EDGAR-CORPUS covers those years by regex on stripped text, unevaluated.
3. **A nested tree below Items** with ordinal paths at fixed global depth and a global
   max-weight sequencing chain. sec-parser and doc2dict produce nesting where styling
   exposes it; neither claims completeness or evaluates it, and sec-parser is unmaintained.
4. **Contract structure.** No open parser does Article/Section/clause for EX-10 credit
   agreements. LEDGAR segments provisions by heading, semi-automatically, for a
   classification benchmark. EX-13 annual reports: nothing found.
5. **Measured precision and recall with a regression gate.** None of the tools publishes an
   accuracy number. The one rule-based number in the literature is macro-F1 0.90 on four core
   items of modern 10-Ks. This project's five-corpus audit at Turn 12: accepted precision
   94.2%, top-level 97.4%, recall gap 11.2%, on windows spanning 1993 text, publisher HTML,
   iXBRL and credit agreements, with every tree change gated on a full-corpus diff.

The one place the literature argues against the design is point 5's flip side: on modern
HTML, a CRF or BERT segmenter beats rules by about eight F1 points. The no-ML-in-the-parser
rule is paid for there and bought back in determinism, offsets and provenance.

## Cheap ways to use them

- **Differential test.** Run edgartools or datamule on the 10-K audit windows from 2001 on
  and count agreement with edgar-agenda's Item boundaries. Disagreements are free leads,
  and the agreement rate is the honest measure of how much of the modern-HTML work is
  duplicated.
- **EDGAR-CORPUS as a 1993-2020 cross-check.** Its item boundaries are regex over stripped
  text, so where the two disagree on a 1990s filing the raw offsets settle it quickly. A
  per-year agreement rate would show where the regex approach fails, which is also where
  this parser's value is.
- **LEDGAR headings** as an external vocabulary check for the contract grammar's Section
  titles.
