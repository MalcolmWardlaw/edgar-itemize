# Evaluation

How the parser's accuracy was measured, what the numbers are at release 1.0.0, and how it
compares with the other open tools. The full evaluation record (the per-turn reports and
decision memos behind every number) ships as a curated set in release 1.0.1; the numbers
below are the ones that record supports.

## Design

Nothing from a language model enters the parser. Language models are used only to
*measure* it, under a fixed protocol:

* **Judge banks.** Windows of filing text around a heading decision (an accepted node or a
  rejected candidate) are drawn out of sample from the full corpora, stratified by era, and
  rated by a panel: a frontier model rates every window; two local models rate them
  independently; the frontier model decides where it rated, the locals decide only where
  they agree, and a third breaks their splits. The protocol was chosen after scoring each
  model against a 100-window hand read.
* **Audit re-score.** One fixed bank of 1,000 windows with stored verdicts is re-scored
  after every change, so precision and recall are comparable across versions rather than
  measured on a fresh sample each time.
* **The gate.** Every change to tree behaviour is measured by a before-and-after diff over
  every document of every corpus (about 229,000 10-Ks, the 10-Qs, 17,000 EX-10 agreements,
  the EX-13 reports), with every lost node enumerated and inspected. No change ships on a
  sample.

## Numbers at 1.0.0

On the fixed 1,000-window audit bank, spanning 1993 plain text, publisher HTML, inline
XBRL and credit agreements:

| measure | value |
|---|---|
| accepted precision (an accepted node is a real heading) | 94.4% |
| top-level precision (Part, Item, Article, Section) | 98.2% |
| recall gap (real headings the parser did not propose) | 11.0% |

Full-corpus sanity on the 10-K panel: `core_complete` (Items 1, 2, 3, 5, 7 and 8 each found
exactly once and in order) is 0.93 to 0.95 per year in the text era (1994 to 2002) and 0.96
to 0.98 from 2003 on; excluding asset-backed trusts and header-only filings it is 0.94 to
0.98 throughout. EX-10 credit agreements: ten or more Sections recovered in 93% (before
2010), 90% (2010 to 2019) and 86% (2020 on) of agreements that are not scanned images.

## Agreement with other tools

Item boundaries were compared with the open tools that extract them, on the audit windows
and on the 1990s panel:

| comparison | agreement |
|---|---|
| datamule, 10-K Items, 2001 on | 91.6% |
| edgartools, 10-K Items, 2001 on | 90.1% |
| EDGAR-CORPUS, 10-K Items, fiscal years 1993 to 2000 | 88.8% to 92.4% per year |

The disagreements were hand-read. Most are contents rows one side accepted, combined
headings ("Items 1 and 2"), and multi-registrant filings; none of the tools was right often
enough on any class to build from, and none publishes an accuracy number of its own. The
text-era agreement with EDGAR-CORPUS is no lower than the HTML-era agreement with the
others, which is the case for one grammar across the eras.

Where the literature argues against the design: on modern HTML, a trained segmenter beats
rules by about eight F1 points on the four core Items. The no-ML rule is paid for there and
bought back in determinism, offsets and provenance. See [related tools](related.md) for
the survey.

## Known limits

* **Recall below the Items.** Sub-headings the parser never proposes (bold party names,
  one-word note titles, statement titles) are the largest class in the recall gap.
* **Multi-registrant 10-Ks.** Where two registrants' item sets are bound together, the
  parser keeps one and rejects the other as `nonmonotone`.
* **Contract clauses.** Clauses outside any Section, and clause runs the sequencer cannot
  place, are written to the rejected table rather than guessed.
* **Scanned exhibits** (page images with a hidden text layer) yield little structure and
  are flagged `image_text`.

These are recorded as leads, not hidden; each next minor version's changelog says which
were addressed and by how much.
