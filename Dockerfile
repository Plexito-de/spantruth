# spantruth in a container: `docker run --rm -v "$PWD:/work:ro" ghcr.io/plexito-de/spantruth:<version> doctor`
#
# spantruth builds one virtual environment per instrumentation library with uv, so the image carries
# uv and Python 3.12, the Python that every subject runs (subjects/_base.toml). It downloads the
# libraries from PyPI on the first run. Mount a volume on /cache to keep them for the next run.
FROM docker.io/library/python:3.12-slim-trixie@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b

COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /uvx /usr/local/bin/

LABEL org.opencontainers.image.source="https://github.com/Plexito-de/spantruth" \
      org.opencontainers.image.description="Checks the values LLM instrumentation libraries report against what the provider actually sent." \
      org.opencontainers.image.licenses="Apache-2.0"

# The subject environments use the image's Python, never a downloaded one. The project is installed
# from uv.lock, without the test and lint groups.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/spantruth \
    PATH=/opt/spantruth/bin:$PATH

COPY pyproject.toml uv.lock README.md LICENSE known_issues.toml prices.toml versions.json /src/
COPY src /src/src
COPY subjects /src/subjects
RUN uv sync --project /src --locked --no-default-groups --no-editable --no-cache \
    && rm -rf /src \
    && spantruth --help > /dev/null

# A user without root. /cache holds the subject environments and uv's package cache.
RUN useradd --create-home --uid 10001 spantruth \
    && mkdir -p /cache /work \
    && chown spantruth /cache
USER spantruth
ENV XDG_CACHE_HOME=/cache
VOLUME /cache
WORKDIR /work

ENTRYPOINT ["spantruth"]
CMD ["--help"]
