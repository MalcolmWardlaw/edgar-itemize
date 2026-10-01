The conformance set shipped inside the package, so `edgar-itemize verify` works from a pip install.
It is a byte-identical copy of `conformance/<version>/` at the repository root (the canonical copy,
with its README); `tests/test_conformance_packaged.py` checks the two are equal.
