"""A pytest plugin: grade the spans of your own LLM wrapper in your own test suite.

    def test_a_stream_gets_its_tokens(spantruth):
        spantruth.run("openai/stream").assert_pass()

pytest loads this module through the `pytest11` entry point in every session where spantruth is
installed, so it imports nothing at load time. The fixtures import `inprocess`, which does the work,
when a test asks for one.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from .inprocess import Spantruth
    from .mockserver import MockServer
    from .receiver import Receiver


def scenario_ids(provider: str | None = None, api: str | None = None) -> list[str]:
    """The scenario IDs, for `pytest.mark.parametrize`. Filter by provider and by OpenAI API.

    Content scenarios and scenarios with several calls are left out: the plugin refuses them,
    because they need a subject's switch or the driver's call windows. `spantruth run` grades them.
    """
    from . import scenarios

    return [
        sid
        for sid, sc in scenarios.SCENARIOS.items()
        if provider in (None, sc.provider)
        and api in (None, sc.api)
        and not (sc.content or sc.calls)
    ]


@pytest.fixture(scope="session")
def spantruth_mock() -> Iterator[MockServer]:
    """The fake provider on 127.0.0.1. It serves every scenario, chosen by the URL path."""
    from .mockserver import MockServer

    with MockServer() as mock:
        yield mock


@pytest.fixture(scope="session")
def spantruth_receiver() -> Iterator[Receiver]:
    """An OTLP/HTTP receiver on 127.0.0.1 that keeps every span in memory."""
    from .receiver import Receiver

    with Receiver() as receiver:
        yield receiver


@pytest.fixture(scope="session")
def _spantruth_session(spantruth_mock, spantruth_receiver) -> Iterator[Spantruth]:
    from .inprocess import session

    with session(spantruth_mock, spantruth_receiver) as st:
        yield st


@pytest.fixture
def spantruth(_spantruth_session) -> Spantruth:
    """Run a scenario against the fake provider and grade the spans your code produced."""
    return _spantruth_session
