# Install

edgar-itemize needs Python 3.11 or later and three libraries: `regex`, `pyarrow` and
`pyyaml`. The parser proper depends on nothing else; the browser viewer and the LLM judge
are optional extras.

## From PyPI

```
pip install edgar-itemize
```

or, with `uv`:

```
uv pip install edgar-itemize
```

!!! note "Release 1.0.0"
    The PyPI distribution and the container image are published with the 1.0.0 tag. A
    pre-release (`1.0.0rc1`) is a development build and promises nothing.

Extras:

```
pip install 'edgar-itemize[viewer]'   # the browser viewer (`edgar-itemize serve`)
pip install 'edgar-itemize[judge]'    # the local-LLM judge client (evaluation tooling only)
```

## As a container

The container image is the frozen reference artifact: a digest-pinned Python base image,
`uv` pinned by digest, every dependency from the lockfile, the release's conformance set
and test fixtures inside. It is what to run when the output must match the release exactly
([reproducibility](reproducibility.md)).

```
docker pull ghcr.io/malcolmwardlaw/edgar-itemize:<version>
docker run --rm -v /path/to/edgar:/data:ro ghcr.io/malcolmwardlaw/edgar-itemize@sha256:<digest> verify --data-root /data
```

Pull by the digest given in the release notes of the version you cite; a tag names a
version, the digest names the exact image.

The image's entry point is the `edgar-itemize` command; any subcommand works the same way
with the EDGAR mirror bind-mounted at `/data`.

## From source

```
git clone https://github.com/MalcolmWardlaw/edgar-itemize
cd edgar-itemize
uv sync --all-extras
uv run pytest -q
```

`uv sync` installs from the lockfile, so a source install has the same dependency versions
as the release. From a clone, run the commands in this manual as `uv run edgar-itemize ...`.

## The data root

The parser reads raw full-submission `.txt` files from a mirror laid out the way the SEC
serves them:

```
$EDGAR_ITEMIZE_DATA_ROOT/
  archives/edgar/data/<CIK>/<accession>.txt     the full-submission files, latin-1
  full-index/<year>/QTR<n>/master.idx           the cached quarterly indexes (written by `manifest`)
  fetch_log.jsonl                               what `fetch` saved, with SHA-256
```

Set `EDGAR_ITEMIZE_DATA_ROOT` to the directory holding `archives/`. There is no default; a
command that needs it exits with a message saying so. `edgar-itemize show <file>` on a single
file needs no data root.

Downloads from the SEC need a `User-Agent` of the form `"<name> <email>"`
(`EDGAR_ITEMIZE_USER_AGENT` or `--user-agent`); this is the SEC's fair-access requirement,
and `manifest` and `fetch` refuse to run without one.

## Verified environments

The release's conformance set of 2,001 documents was parsed under CPython 3.11, 3.12, 3.13
and 3.14 on the host and under the container's interpreter, with identical output hashes.
The release notes of each version list the interpreters that agree; if one ever differs,
the container is the reference. The HTML tokeniser is vendored from CPython 3.11.15 for
this reason ([reproducibility](reproducibility.md), "The environment is part of the version").

## Check the install

```
edgar-itemize verify --data-root /path/to/edgar
```

re-parses the shipped conformance set against your mirror and prints one line with the
input and output hash counts. Exit code 0 means every present input matched the release's
hash and every output hash equals the shipped one. A filing your mirror lacks is reported
as `input missing` and does not fail the check.
