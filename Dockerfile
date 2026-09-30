# syntax=docker/dockerfile:1
# edgar-itemize container image (docs/RELEASE_PLAN.md section 4 item 8). The image is the
# frozen reference artifact: base image and uv are pinned by digest, dependencies come from
# uv.lock (`uv sync --frozen`), the core package only (no viewer/judge extras). The release's
# conformance set is inside, so an install is checked against a bind-mounted mirror with
#
#   docker run --rm -v /path/to/edgar:/data:ro ghcr.io/malcolmwardlaw/edgar-itemize:<tag> \
#       verify --data-root /data --workers 8
#
# and a parse with `... parse --data-root /data ...`. The entrypoint is the `edgar-itemize` CLI.

ARG BASE=python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

FROM ${BASE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.10.8@sha256:88234bc9e09c2b2f6d176a3daf411419eb0370d450a08129257410de9cfafd2a /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/edgar-itemize
WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY LICENSES ./LICENSES
COPY src ./src
# Core dependencies and the package itself (built with uv_build, installed non-editable so
# the runtime stage needs no source tree). --frozen: uv.lock as is, never re-resolved.
RUN uv sync --frozen --no-dev --no-editable

FROM ${BASE} AS runtime
LABEL org.opencontainers.image.source="https://github.com/MalcolmWardlaw/edgar-itemize" \
      org.opencontainers.image.title="edgar-itemize" \
      org.opencontainers.image.description="Deterministic agenda-structure parser for SEC EDGAR filings" \
      org.opencontainers.image.licenses="MIT"
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin itemize
COPY --from=builder /opt/edgar-itemize /opt/edgar-itemize
# The conformance set (manifest + expected hashes, ~260 KB) and the test fixtures its
# `fixtures` rows resolve to, laid out as in the repository so `verify` finds them from /app.
WORKDIR /app
COPY --chown=itemize:itemize conformance ./conformance
COPY --chown=itemize:itemize tests/data ./tests/data
ENV PATH="/opt/edgar-itemize/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER itemize
ENTRYPOINT ["edgar-itemize"]
CMD ["--help"]
