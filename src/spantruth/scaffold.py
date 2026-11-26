"""`spantruth new subject`: the two files of a subject, from a template that loads as it is.

The template runs, and grades nothing as passing: its `instrument()` attaches nothing, so every cell
says `span.recorded: missing` until you put the library's documented setup in.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import scenarios

NAME = re.compile(r"[a-z0-9][a-z0-9-]*")

SUBJECT_TOML = """\
title = "{name}"
# The library's home page or repository. It must start with https:// or http://.
homepage = "https://example.com/{name}"
# The providers whose SDK calls the library instruments. Remove the ones it does not.
providers = [{providers}]
# The library's packages, pinned, such as "my-instrumentation==1.2.3". They are installed next to
# the provider SDKs and the OpenTelemetry SDK in subjects/_base.toml.
packages = []
# The package names that the nightly run of the latest versions unpins.
track = []
"""

INSTRUMENT_PY = '''\
"""Attach {name} the way its documentation shows first."""


def instrument(tracer_provider, otlp_endpoint):
    """Set the library up, pointed at tracer_provider or at otlp_endpoint.

    spantruth then makes the SDK calls of each scenario and grades the spans that arrive. Return
    nothing, or a list of extra tracer providers to flush when the library makes its own.
    """
    return None
'''


class ScaffoldError(ValueError):
    """A name that is not a subject name, or a directory that is there already."""


def new_subject(name: str, parent: Path) -> Path:
    """Write `<parent>/<name>/subject.toml` and `instrument.py`. Return the directory."""
    if not NAME.fullmatch(name):
        raise ScaffoldError(
            f"{name!r} is not a subject name: use lowercase letters, digits and dashes"
        )
    directory = parent / name
    if directory.exists():
        raise ScaffoldError(f"{directory} exists already")
    providers = sorted({sc.provider for sc in scenarios.SCENARIOS.values()})
    directory.mkdir(parents=True)
    (directory / "subject.toml").write_text(
        SUBJECT_TOML.format(name=name, providers=", ".join(f'"{p}"' for p in providers))
    )
    (directory / "instrument.py").write_text(INSTRUMENT_PY.format(name=name))
    return directory
