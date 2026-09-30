# Citing

Name the version you ran, because that is what the promise is stated over. In a paper:

> Filing structure was extracted with edgar-itemize v1.0.0 (Wardlaw, 2026), a deterministic
> parser; the parsed node tables reproduce on the release's conformance set (`edgar-itemize
> verify`, 2,001 of 2,001 documents).

The citation metadata lives in the repository as `CITATION.cff`, which GitHub renders as a
"Cite this repository" box and Zenodo reads when a release is archived. The concept DOI,
which resolves to every version, and each version's own DOI are added there at the first
Zenodo mint.

```yaml
--8<-- "CITATION.cff"
```

## The dataset

The parsed node tables for the full 10-K and 10-Q corpora (offsets only, no filing text)
are published per release under CC-BY-4.0 with the release's version and input hashes, so
"we used the v1.0.0 dataset" is as citable as "we ran v1.0.0". Offsets into public filings
are facts, not a redistribution of the filings.
