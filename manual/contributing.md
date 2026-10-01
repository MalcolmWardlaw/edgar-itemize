--8<-- "CONTRIBUTING.md"

## Sending back judgements

If you have reviewed a set of the parser's output, whether by hand or with a model of your
own, the verdicts are welcome even when you have no fix to offer. A file format for
exchanging verdicts is planned for release 1.0.1. Until then, send them as an issue or a
pull request with one row per verdict carrying the accession number, the document sequence,
the edgar-itemize version, and the byte offsets of the node or heading concerned, with what
you judged right or wrong and, where you can, what you expected instead. Confirmed errors
are recorded in [errata](errata.md) and fixed in the next release, as above.
