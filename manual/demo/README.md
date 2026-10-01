# The static demo

Not part of the manual's navigation (excluded in `mkdocs.yml`); this note records what the
demo is built from. The page is `index.html` here; the published site serves it at
`demo/`. The Viewer tab loads `demo/viewer.html`, which the build hook (`manual/_hooks.py`,
`on_post_build`) writes from `src/edgar_itemize/viewer/static/index.html` with one line
added: `<script src="shim.js"></script>`. `shim.js` answers the viewer's `/api/doc`,
`/api/original` and `/api/raw` calls from the files under `data/`, answers `/api/corpora`
and `/api/search` with empty results, returns 404 for everything else, and hides the
search box, parse switch, browse drawer, review and gold modes.

## Rebuilding the data

```
export EDGAR_ITEMIZE_DATA_ROOT=/path/to/edgar      # holds archives/edgar/data/<cik>/<accession>.txt
uv run python scripts/demo_bake.py --out manual/demo/data --spec manual/demo/bake.json
```

`bake.json` lists the documents and the run each is read from (the 1.0.0 reference runs
`runs/full_ref_10k`, `full_ref_10q`, `full_ref_ex10`, `full_ref_ex13`). The bake calls the
viewer app in process (FastAPI TestClient) with `source=run`, so `doc.json` and
`original.html` are byte for byte what `edgar-itemize serve` returns for that document
read from that run; `raw.txt` is the document's `<TEXT>` payload (latin-1 decoded, stored
as UTF-8), from which the shim slices `/api/raw` ranges. `data/` is 13.9 MB.

## The documents (nine)

| key | filer | form, filed | era | why |
|---|---|---|---|---|
| 0000320193-94-000016 | Apple Computer | 10-K, 1994 | text | 1990s plain-text, self-filed 10-K; panel year 1 |
| 0000320193-95-000016 | Apple Computer | 10-K, 1995 | text | panel year 2 |
| 0000320193-96-000023 | Apple Computer | 10-K, 1996 | text | panel year 3 |
| 0000950152-06-007851 | Thor Industries | 10-K, 2006 | html_publisher (Bowne) | 2000s financial-printer HTML, has Item 1A and a full Item 7 (558 KB) |
| 0000320193-24-000123 | Apple Inc. | 10-K, 2024 | ixbrl (Workiva) | inline XBRL; the same filer 30 years after the panel |
| 0000012927-02-000010 | Boeing | 10-Q, 2002 | html_early | 10-Q Part I / Part II items, and the one early-HTML document |
| 0000950144-97-003175, seq 4 | AGCO | EX-10.17, 1997 | text | credit agreement: Section 5.01 Affirmative and 5.02 Negative Covenants with 12 and 16 children, 759 nodes, depth 8 |
| 0001193125-08-240082, seq 2 | Boeing | EX-10.1, 2008 | html_publisher (Donnelley) | HTML credit agreement: Article 4 covenants with nested clauses, 352 nodes |
| 0000021076-95-000005, seq 2 | Clorox | EX-13, 1995 | text | annual report exhibit; MD&A heading with sub-sections (70 KB) |

Selection: candidates were drawn from the reference runs' `documents-*` and `nodes-*`
tables. For the panel, three consecutive 10-Ks of one well-known filer with Item 7 found in
all three and a full MD&A in the 10-K itself (most 2000s financial-printer 10-Ks of large
filers incorporate MD&A by reference from the annual report, so Item 7 is a stub; the
1994-1996 Apple 10-Ks carry it, at 19-28% of the document). The plain-text panel keeps the
data small; the publisher-HTML and inline-XBRL examples are separate documents, which is
why there are nine rather than eight. The credit agreements were ranked by clause-node
count among those with both an Affirmative and a Negative Covenants node. Size bound: raw
payloads of 70 KB to 1.5 MB (the inline-XBRL 10-K is the largest; no inline-XBRL 10-K of a
well-known filer in the run is under 1.4 MB).
