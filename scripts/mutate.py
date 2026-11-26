#!/usr/bin/env python3
"""Mutation test of the checks: every small fault planted in the grader must fail a fast test.

Purpose: the checks decide every verdict. A test suite that still passes when a comparison flips
         or a convention name goes missing does not test that line. This script applies a fixed
         list of such mutants to checks.py and conventions.py, one at a time, in a copy of the
         repository, runs the fast tests, and fails when any mutant survives.
Usage:   uv run python scripts/mutate.py [--only NAME] \
           [--wrap "run-capped --mem 1500 --time 300 --"]

Exit: 0 every mutant was killed, 1 a mutant survived, 2 the unmutated copy does not pass its tests
or a mutant no longer applies (its line changed: update the list).
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECKS = "src/spantruth/checks.py"
CONVENTIONS = "src/spantruth/conventions.py"
SKIP = shutil.ignore_patterns(".git", ".venv", ".xdg-cache", "__pycache__", "*.pyc", "node_modules")
TIMEOUT = 300


@dataclass(frozen=True)
class Mutant:
    name: str
    path: str
    old: str
    new: str


MUTANTS = (
    # Flipped comparisons.
    Mutant("single-report-off-by-one", CHECKS, "len(reporting) > 1", "len(reporting) > 2"),
    Mutant("orphans-flipped", CHECKS, "s.trace_id != parent", "s.trace_id == parent"),
    Mutant("error-branch-flipped", CHECKS, "if t.error is not None:", "if t.error is None:"),
    Mutant("unread-flipped", CHECKS, "if v in t.unread_counts:", "if v not in t.unread_counts:"),
    Mutant(
        "open-span-flipped",
        CHECKS,
        'if r["end_ns"] is None and sid',
        'if r["end_ns"] is not None and sid',
    ),
    Mutant(
        "token-equality-loosened",
        CHECKS,
        "isinstance(v, (int, float)) and v == expected",
        "isinstance(v, (int, float)) and v >= expected",
    ),
    Mutant(
        "model-name-fallback-flipped",
        CHECKS,
        "ok = v == t.request_model",
        "ok = v != t.request_model",
    ),
    # Off by one, or a wrong sign, in a sum.
    Mutant(
        "formula-both-left-out",
        CHECKS,
        "total - read - write:",
        "total - read - write + 1:",
    ),
    Mutant("formula-counted-twice", CHECKS, "total + read + write:", "total + read - write:"),
    Mutant("formula-doubled", CHECKS, "2 * total:", "3 * total:"),
    Mutant(
        "formula-reasoning-sign",
        CHECKS,
        "if got == t.output_tokens - t.reasoning_tokens:",
        "if got == t.output_tokens + 1 - t.reasoning_tokens:",
    ),
    Mutant(
        "short-write-off-by-one",
        CHECKS,
        "short_write = write - long_write",
        "short_write = write - long_write - 1",
    ),
    # A convention name or alias dropped.
    Mutant(
        "drop-sentry-cache-read",
        CONVENTIONS,
        '"gen_ai.usage.input_tokens.cached",  # Sentry',
        "",
    ),
    Mutant(
        "drop-ddtrace-cache-write",
        CONVENTIONS,
        '"gen_ai.usage.cache_write_input_tokens",  # ddtrace',
        "",
    ),
    Mutant(
        "drop-openinference-reasoning",
        CONVENTIONS,
        '"llm.token_count.completion_details.reasoning",',
        "",
    ),
    Mutant(
        "drop-operation-marker",
        CONVENTIONS,
        '{"gen_ai.operation.name", "langfuse.observation.type"}',
        '{"langfuse.observation.type"}',
    ),
    Mutant("drop-max-tokens-alias", CONVENTIONS, '"max_tokens": "length",', ""),
    Mutant("drop-tool-calls-alias", CONVENTIONS, '"tool_calls": "tool_call",', ""),
    Mutant(
        "response-id-in-openinference",
        CONVENTIONS,
        '"gen_ai.response.id", in_openinference=False',
        '"gen_ai.response.id", in_openinference=True',
    ),
    Mutant(
        "openinference-only-loosened",
        CONVENTIONS,
        'return any(k.startswith("llm.") for k in keys) and not any(',
        'return any(k.startswith("llm.") for k in keys) or not any(',
    ),
    Mutant(
        "finish-order-matters",
        CONVENTIONS,
        "return sorted(FINISH_ALIASES.get(v, v) for v in values)",
        "return list(FINISH_ALIASES.get(v, v) for v in values)",
    ),
    Mutant(
        "json-path-first-key-only",
        CONVENTIONS,
        'for part in key.split("."):',
        'for part in key.split(".")[:1]:',
    ),
)


SELF_TEST = "tests/test_mutate.py"


def _tests(copy: Path, wrap: list[str]) -> tuple[int, float]:
    """Run the fast tests in the copy, stopping at the first failure."""
    env = os.environ | {"PYTHONPATH": str(copy / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    command = [*wrap, sys.executable, "-m", "pytest", "-x", "-q", "-m", "not slow"]
    # test_mutate.py checks that each mutant's text is in its file, so in a mutated copy it always
    # fails, and it would kill every mutant whatever the other tests do.
    command += ["-p", "no:cacheprovider", f"--ignore={SELF_TEST}", "tests"]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            command, cwd=copy, env=env, capture_output=True, text=True, timeout=TIMEOUT + 30
        )
        code = proc.returncode
    except subprocess.TimeoutExpired:
        code = 124
    return code, time.monotonic() - start


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--wrap", default="", help="a prefix for the test command, such as a cap")
    parser.add_argument("--only", action="append", help="run only this mutant, repeatable")
    args = parser.parse_args(argv)
    wrap = shlex.split(args.wrap)
    chosen = [m for m in MUTANTS if not args.only or m.name in args.only]
    with tempfile.TemporaryDirectory(prefix="spantruth-mutants-") as tmp:
        copy = Path(tmp) / "repo"
        shutil.copytree(REPO, copy, ignore=SKIP, symlinks=True)
        code, seconds = _tests(copy, wrap)
        print(f"unmutated: exit {code} in {seconds:.0f} s", file=sys.stderr)
        if code != 0:
            print("the unmutated copy does not pass its fast tests", file=sys.stderr)
            return 2
        survivors = []
        for m in chosen:
            path = copy / m.path
            source = path.read_text()
            if source.count(m.old) != 1:
                print(f"{m.name}: the text to mutate is not in {m.path} once", file=sys.stderr)
                return 2
            path.write_text(source.replace(m.old, m.new))
            try:
                code, seconds = _tests(copy, wrap)
            finally:
                path.write_text(source)
            # Exit 1 is a failing test. Anything else nonzero (a crash, a cap) also kills it.
            verdict = "killed" if code != 0 else "SURVIVED"
            print(f"{m.name}: {verdict} (exit {code}, {seconds:.0f} s)", flush=True)
            if code == 0:
                survivors.append(m.name)
    if survivors:
        print(f"{len(survivors)} of {len(chosen)} mutants survived: {', '.join(survivors)}")
        return 1
    print(f"all {len(chosen)} mutants were killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
