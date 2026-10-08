# Related EDGAR structure parsers (survey, 2026-09-26)

This survey asks one question: how much of edgar-itemize duplicates existing work. It
covers the open-source tools, datasets, commercial services and papers that recover
structure (Parts, Items, sections) from SEC EDGAR filings. The facts for each entry were
taken from the project's README, PyPI page or paper as read on 2026-09-26; licence and
maintenance status are as of that day. Measured agreement between edgar-itemize and the
tools that were run as comparators is reported on the evaluation page, not here.

## 1. The field

Entries are grouped by what they do. "Structure" describes the shape of the output; "accuracy
published" records whether the project reports any measurement of its own segmentation.

### 1.1 Item extraction from HTML-era 10-K and 10-Q filings

| project | input and method | forms | structure | accuracy published | licence and status |
|---|---|---|---|---|---|
| [edgar-crawler](https://github.com/lefterisloukas/edgar-crawler) (Loukas et al., AUEB; WWW 2025) | HTML stripped to text; regular expressions on Item headers | 10-K Items 1 to 16 including 1A, 1B, 1C and 9A to 9C; 10-Q Parts and Items; 8-K Items 1.01 to 9.01 | flat: one string per Item; older 10-Qs fall back to whole-Part text | none | GPL-3.0; last push 2025-07-18 |
| [edgartools](https://github.com/dgunning/edgartools) | general EDGAR client: XBRL, ownership forms, section extraction | more than twenty form types; section extraction for 10-K and 10-Q | Items exposed as attributes (Item 1A, Item 7) | none | MIT; actively maintained |
| [sec-parser](https://github.com/alphanome-ai/sec-parser) (Alphanome) | HTML to a semantic tree of titles, paragraphs and tables under Part and Item | 10-Q demonstrated; 10-K, 8-K and S-1 mentioned | tree of elements; the detection mechanism is not documented | none | MIT; README states the project is no longer maintained |
| [sec-parsers](https://pypi.org/project/sec-parsers/), succeeded by [datamule](https://github.com/john-friedman/datamule-python) with [doc2dict](https://github.com/john-friedman/doc2dict) (Friedman) | header detection from tags, CSS, emphasis and position; a nested dictionary; mapping dictionaries place Item under Part | 10-K, 10-Q, 8-K, S-1, 20-F | nested dictionary, with sub-headers where styling exposes them | none | sec-parsers last release 0.549 (2024-07-29), described as work in progress; datamule actively maintained |
| [sec2md](https://pypi.org/project/sec2md/) | HTML to Markdown with PART and ITEM boundaries, page-aware, XBRL tags retained; intended for retrieval-augmented generation | 10-K (18 Items), 10-Q (11), 8-K (41), 20-F, SC 13D/G | Items as Markdown sections | none | MIT; 0.1.23, 2026-03-15 |
| [pipeline-sec-filings](https://github.com/Unstructured-IO/pipeline-sec-filings) (Unstructured) | section extraction from inline XBRL by predefined section names or user-supplied regular expressions | 10-K, 10-Q, S-1 | named sections | none | archived 2024-05-22 |
| [edgar-parser](https://github.com/henrysouchien/edgar-parser), [sec-edgar-toolkit](https://github.com/stefanoamorelli/sec-edgar-toolkit), [ETL-SEC-EDGAR-10-k-Filings](https://github.com/pChitral/ETL-SEC-EDGAR-10-k-Filings) | Item or MD&A extraction from HTML | mainly 10-K | flat | none | small projects |

A representative description of the style-based recipe that sec-parser, doc2dict and sec2md
embody is the [AlphaCreek write-up](https://www.alphacreek.ai/blog/how-to-parse-sec-10k-filings-into-sections)
(2026): each child of `<body>` becomes a block; PART and ITEM are found by anchored patterns,
preferring the body occurrence over the table of contents; sub-headings come from inline CSS
weight and colour; blocks nest under the nearest preceding title of equal or higher rank. It
illustrates the approach on one filing and reports no evaluation.

### 1.2 Tools that address the text era (1993 to 2000)

| project | input and method | forms | structure | accuracy published | licence and status |
|---|---|---|---|---|---|
| [SEC-EDGAR-text](https://github.com/alions7000/SEC-EDGAR-text) | key sections of 10-K and 10-Q located by configurable search terms; the only tool found that claims compatibility "with all main EDGAR document formats from 1993 onwards" | 10-K, 10-Q | section excerpts | none; the README's own assessment is "generally accurate in extracting text, but lots of room for improvement" | GPL-3.0; last push 2026-02-01 |
| [edgar](https://cran.r-project.org/package=edgar) (R, CRAN; Lonare, SoftwareX 2021) | regular expressions over text for Item 1 (`getBusinDescr`) and Item 7 (`getMgmtDisc`) | 10-K, 10-K405, 10-KSB, 10-KSB40 | two Items as text | none | widely used in finance research; last push 2024-04-03 |
| [Loughran-McDonald Stage One 10-X](https://sraf.nd.edu/sec-edgar-data/cleaned-10x-files/10x-stage-one-parsing-documentation/) | strips HTML, PDF and image content from every 10-K and 10-Q variant, 1993 to 2024 | 10-X | none: a cleaned document, no Items | not applicable | data files, periodically refreshed |

### 1.3 Section and contract structure

| project | input and method | forms | structure | accuracy published | licence and status |
|---|---|---|---|---|---|
| [LexNLP](https://github.com/LexPredict/lexpredict-lexnlp) (LexPredict / ContraxSuite) | `get_section_spans` returns `DocumentSection` objects with `start`, `end`, `title_start` and `title_end` character offsets and a `level`; detection by a scikit-learn line classifier (line length, character classes, case, the keywords "section" and "article", a window of neighbouring lines) with a regular-expression fallback; `SectionLevelParser` defines nine hierarchy levels (appendix, exhibit, schedule, part, title; subtitle; section; subsection; article; upper-case letters; dotted numerals; letters; parentheticals) | contracts and legal documents generally; trained and unit-tested on EDGAR documents | a flat list of sections with levels, not a tree | none | AGPL-3.0; release 2.3.0 (2023), last push 2024-05-27 |
| [LEDGAR](https://aclanthology.org/2020.lrec-1.155/) (Tuggener et al., LREC 2020) | provisions of EX-10 material contracts segmented by heading, semi-automatically, and labelled with about 100 provision types | EX-10 | provision segments; the paper discusses the noise in the segmentation | classification benchmarks only; the segmentation itself is not evaluated | dataset |

LexNLP is the one precedent for a section grammar over legal text with offsets. Its output
is a flat list with levels rather than a tree and it has not been released since 2023. Like
every project in this survey, it was not used as a component.

### 1.4 Datasets of pre-split filings

| dataset | contents | split method | accuracy published | licence and status |
|---|---|---|---|---|
| [EDGAR-CORPUS](https://arxiv.org/abs/2109.14394) (Loukas et al., 2021) | 10-K Items, fiscal years 1993 to 2020, as JSON | the edgar-crawler pipeline: "html-stripped, cleaned and split into the different items by using regular expressions" | none for the split; the paper evaluates downstream embeddings | dataset on Hugging Face |
| [PleIAs/SEC](https://huggingface.co/datasets/PleIAs/SEC) | EDGAR-CORPUS extended to 2024; the 2021 to 2024 portion "collected using the EDGAR-Crawler toolkit" | as above | none; no validation described | CC0 |
| [sec-api Form 10-K dataset](https://sec-api.io/datasets/form-10k-content) | 305,479 10-K variants, 1993 to present, 34 GB, documents "as accepted by EDGAR"; the pre-2001 portion described as "plain-text ASCII wrapped in SGML" | no Item split | none | commercial |

Mirrors and derivatives of EDGAR-CORPUS on Hugging Face (`eloukas/edgar-corpus`;
`khaihernlow/financial-reports-sec`, sentence-split) and the downstream NLP corpora built on
EDGAR text (risk-factor section datasets, KPI-EDGAR, BUSTER, BeanCounter) were examined and
do not themselves recover structure.

### 1.5 Download and indexing tools, and commercial services

| project | scope | licence and status |
|---|---|---|
| [pyedgar](https://github.com/gaulinmp/pyedgar) | download and index; splits the raw submission at `<DOCUMENT>` boundaries and stops there | open source |
| [py-sec-edgar](https://github.com/ryansmccoy/py-sec-edgar) | download and index only | open source |
| [OpenEDGAR](https://github.com/LexPredict/openedgar) (LexPredict, 2018) | ingestion and database loading | open source |
| [sec-api.io Extractor](https://github.com/janlukasschroeder/sec-api-python) | Items by name over a hosted API | 10-K, 10-Q, 8-K and other forms; flat Items; no public accuracy statement | commercial |

### 1.6 Literature that measures Item segmentation

Two papers evaluate Item segmentation itself rather than a downstream task.

- [Utilizing Pre-trained and Large Language Models for 10-K Items Segmentation](https://arxiv.org/abs/2502.08875)
  (2025). 3,737 annotated 10-Ks; the core Items 1, 1A, 3 and 7. Macro-F1: rule-based
  0.9048, conditional random field 0.9818, BERT with Bi-LSTM 0.9825, GPT-4o with line
  identifiers 0.9567. This is the only measured comparison of segmentation methods found.
- [From Rules to Flexibility](https://dl.acm.org/doi/10.1145/3746252.3761609) (CIKM 2025).
  Argues that rule-based extraction has degraded on post-2021 10-K layouts and proposes
  fuzzy matching with structural heuristics. The paper is paywalled and its figures were not
  read for this survey.

## 2. Where edgar-itemize overlaps

The top-level division of a 2001-onward HTML 10-K or 10-Q into Items is performed by at
least six open tools and one commercial API, and EDGAR-CORPUS already distributes 10-K
Items for fiscal years 1993 to 2020. A project whose deliverable were "the text of Item 7
for modern 10-Ks" would duplicate them.

## 3. Where it differs

None of the projects above does all of the following, and most do none of them.

1. **Byte offsets into the raw submission as the coordinate system.** Every tool surveyed
   returns text; none returns addresses into the filing. edgar-itemize addresses every node
   by byte offset, records the rule identifiers that placed it, and writes every rejected
   candidate with its reason.
2. **One grammar across the three format eras.** Tools built on HTML degrade by
   construction on the 1993 to 2000 plain-text and SGML filings, which is where the long
   sample of a finance panel lies. EDGAR-CORPUS covers those years by regular expressions
   over stripped text, without evaluation.
3. **A nested tree below the Item level**, with ordinal paths at a fixed global depth and a
   global maximum-weight sequencing chain. sec-parser and doc2dict produce nesting where the
   styling exposes it; neither claims completeness or evaluates it, and sec-parser is
   unmaintained.
4. **Contract structure.** No open parser recovers Article, Section and clause structure for
   EX-10 credit agreements. LEDGAR segments provisions by heading, semi-automatically, for a
   classification benchmark. Nothing was found for EX-13 annual reports.
5. **Measured precision and recall behind a regression gate.** None of the tools publishes
   an accuracy figure for its own segmentation. The one rule-based figure in the literature
   is a macro-F1 of 0.90 on four core Items of modern 10-Ks. edgar-itemize's audit at 1.0.0
   (accepted precision 94.4%, top-level precision 98.2%, recall gap 11.0% on 1,000 windows
   spanning 1993 text, publisher HTML, inline XBRL and credit agreements) is on the evaluation
   page, and every change to tree behaviour is gated on a full-corpus diff.

The literature does argue against one aspect of the design. On modern HTML, a trained
segmenter (CRF or BERT) outperforms rules by about eight F1 points on the four core Items.
The decision to keep machine learning out of the parser costs accuracy there and is
repaid in determinism, offsets and provenance.

## 4. How these tools were used

Six of them, edgartools, datamule, doc2dict, EDGAR-CORPUS, LexNLP and sec-parser, were
run as isolated comparators on samples of the corpora, each in its own environment and
called only as a subprocess or read as data; the regular expressions and heading cues of
the others were read and compared with the grammar's own. Every disagreement with
edgar-itemize was hand-read, and the agreement rates are reported on the evaluation page.
No code or pattern from any of them entered the parser.
