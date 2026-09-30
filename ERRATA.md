# Errata

Known wrong output in released versions of edgar-itemize.

A tagged release is never modified, re-tagged or deleted, and nothing is yanked from PyPI
(`docs/VERSIONING.md` section 3): the version a paper cites keeps producing exactly what it
produced. So when a released version is found to parse some filings wrongly, the error is
recorded here against that version, with the affected accessions, and the fix ships in the
next minor version. A reader of a paper that cites version X can check this table for X and
know which documents to treat with care.

An entry is added when a wrong parse is confirmed by reading the filing (an issue with the
accession, sequence, version and expected result; `CONTRIBUTING.md`). "Wrong" means a node or
rejected row that contradicts the filing's own headings, not a legitimate absence (Regulation
AB trusts, incorporation-by-reference stubs, omission statements, header-only submissions).

Columns: the version(s) affected; the corpus (10-K, 10-Q, EX-10, EX-13, text); what is wrong,
in one sentence, with the rule id if one is at fault; the accession list (inline when short,
else a path to a parquet or text file of `accession_number, sequence` under
`errata/`); the version that fixed it; the issue or memo that documents it.

| affected versions | corpus | what is wrong | accessions | fixed in | reference |
|---|---|---|---|---|---|
| | | | | | |
