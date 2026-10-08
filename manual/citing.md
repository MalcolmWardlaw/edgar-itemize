# Citing

Name the version you ran, because that is what the promise is stated over. In a paper:

> Filing structure was extracted with edgar-itemize v1.0.0 (Wardlaw, 2026), a deterministic
> parser; the parsed node tables reproduce on the release's conformance set (`edgar-itemize
> verify`, 2,001 of 2,001 documents).

The citation metadata lives in the repository as `CITATION.cff`, which GitHub renders as a
"Cite this repository" box and Zenodo reads when a release is archived. The concept DOI
[10.5281/zenodo.23229031](https://doi.org/10.5281/zenodo.23229031) resolves to every
version; v1.0.0's own record is [10.5281/zenodo.23229032](https://doi.org/10.5281/zenodo.23229032)
(archived 2026-10-08).

```yaml
--8<-- "CITATION.cff"
```

## The dataset

The parsed tables for the full 10-K and 10-Q corpora (`nodes`, `documents` and `rejected`;
byte offsets and heading lines, no filing body text) are published per release under
CC-BY-4.0 with the release's version and input hashes, so "we used the v1.0.0 dataset" is as
citable as "we ran v1.0.0". Offsets into public filings are facts, not a redistribution of
the filings.

The 1.0.0 dataset is [10.5281/zenodo.23241710](https://doi.org/10.5281/zenodo.23241710)
(2.55 GB, 13 files, published 2026-10-08); its concept DOI
[10.5281/zenodo.23241709](https://doi.org/10.5281/zenodo.23241709) resolves to the newest
dataset version. Cite the software and name the dataset version you used.
