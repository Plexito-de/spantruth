"""The spans of the Acme wrapper, graded by the spantruth pytest plugin. See docs/pytest.md.

Install: `pip install spantruth[pytest] openai`. Run: `pytest tests`.
"""

import openai
import pytest
from acme_llm import traced
from openai.resources.chat.completions import Completions

from spantruth.pytest_plugin import scenario_ids

# The scenarios the wrapper gets right today. Every other one shows a known bug, and is marked
# xfail(strict=True): when a fix makes one pass, the run fails until you move it here.
PASSING = {"openai/basic", "openai/tool-call", "openai/retry-429"}


@pytest.fixture(autouse=True)
def acme(monkeypatch):
    """Attach the wrapper the way the application does, for one test."""
    monkeypatch.setattr(Completions, "create", traced(Completions.create))


@pytest.mark.parametrize(
    "scenario",
    [
        sid if sid in PASSING else pytest.param(sid, marks=pytest.mark.xfail(strict=True))
        for sid in scenario_ids("openai")
    ],
)
def test_the_spans_match_the_wire(spantruth, scenario):
    spantruth.run(scenario).assert_pass()


def test_a_cache_hit_counts_the_cached_tokens_twice(spantruth):
    with spantruth.call("openai/cache-hit") as call:
        client = openai.OpenAI(base_url=call.base_url, api_key="test")
        client.chat.completions.create(model=call.scenario.request_model, messages=[])
    assert call.result.failing == {"input_tokens": "wrong", "cache_read_tokens": "missing"}
    (tokens,) = [c for c in call.result.checks if c.id == "input_tokens"]
    assert (tokens.expected, tokens.got) == (5379, 9475)
