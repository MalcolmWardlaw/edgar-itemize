# Demo

**[Open the demo](demo/index.html)**: a static page, no server behind it.

The demo is the pre-baked output of release 1.0.0 for nine public SEC filings, read from the
release's reference runs and shown through the parser's own browser viewer. Three of the
nine are consecutive Apple 10-Ks (1994 to 1996, plain text); the others are a 2000s
financial-printer HTML 10-K (Thor Industries), an inline-XBRL 10-K (Apple, 2024), a 10-Q
(Boeing, 2002), two credit agreements filed as EX-10 exhibits (AGCO 1997 in plain text,
Boeing 2008 in HTML) and an EX-13 annual report (Clorox, 1995). Pick a filing at the top
left; the tabs show it five ways:

| tab | what it shows |
|---|---|
| Viewer | the filing's text with the agenda tree as colour bands, the tree and the rejected candidates in the sidebar, and the original document side by side (tick *original*) |
| Outline | every node of the tree with its depth, ordinal path, `label_canon`, title, `raw_start`, confidence and `rule_ids`; a click opens the Viewer at the node |
| Pulled items | the sections a researcher would extract (Items 1A and 7 of a 10-K, the covenant articles and their clauses of a credit agreement), each with its byte span and the first 300 characters of `raw[raw_start:raw_end]`, which is what `examples/pull_items.py` writes for a whole run |
| Rejected | the heading candidates that did not enter the tree, with the reason and score of each |
| Panel | Item 7 located in each of the three Apple 10-Ks: start, end, length and share of the document |

Every offset is a byte offset into the raw submission file as the SEC serves it, so any
number on the page can be checked against the filing on EDGAR.

## The real viewer

The demo answers the viewer's requests from files; the viewer itself parses on demand from
your own mirror of the archive:

```
pip install 'edgar-itemize[viewer]'
export EDGAR_ITEMIZE_DATA_ROOT=/data/edgar    # holds archives/edgar/data/<cik>/<accession>.txt
edgar-itemize serve                           # http://127.0.0.1:8765
```

See the [quickstart](quickstart.md#6-the-viewer) and the [command line](cli.md#serve)
reference. The same nine filings can be fetched and parsed on your machine with the sample
manifests in `examples/` ([quickstart](quickstart.md#first-the-starter-set)). The demo files are rebuilt with `scripts/demo_bake.py` from a release's
reference runs.
