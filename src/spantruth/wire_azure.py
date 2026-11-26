"""Azure OpenAI wire format: the OpenAI Chat Completions shapes, as Azure serves them.

Azure differs from OpenAI in three ways that an instrumentation meets:

- The request names a deployment, not a model: `/openai/deployments/{deployment}/chat/completions
  ?api-version=...`. The SDK sends the deployment as `model`, so `gen_ai.request.model` is the
  deployment name and the response's `model` is the model behind it.
- Azure adds its content filter results: `prompt_filter_results` on the response and
  `content_filter_results` on each choice. The `openai` SDK types do not declare them.
- A stream starts with a chunk that holds only the prompt filter results: an empty `id`, an empty
  `model` and no choices. A library that reads the id or the model from the first chunk records
  an empty string. The "Prompt annotation message" of this page shows the chunk:
  https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/content-streaming

Shapes follow the Azure OpenAI REST reference,
https://learn.microsoft.com/en-us/azure/ai-foundry/openai/reference.
"""

from __future__ import annotations

from dataclasses import replace

from . import wire_openai as oa
from .model import Event, Response, Truth

API_VERSION = "2024-10-21"
# The fields Azure adds that the `openai` SDK types do not declare, as dotted paths with the list
# indexes left out. tests/test_wire.py accepts these on Azure fixtures and no others.
AZURE_ONLY = frozenset({"prompt_filter_results", "choices.content_filter_results"})

_SAFE = {"filtered": False, "severity": "safe"}
_FILTERS = {name: _SAFE for name in ("hate", "self_harm", "sexual", "violence")}
PROMPT_FILTER = [{"prompt_index": 0, "content_filter_results": _FILTERS}]


def completion(**kw) -> Response:
    """A Chat Completions body with Azure's filter results added."""
    body = oa.completion(**kw).body
    choices = [c | {"content_filter_results": _FILTERS} for c in body["choices"]]
    return Response(body=body | {"choices": choices, "prompt_filter_results": PROMPT_FILTER})


def chunks(**kw) -> Response:
    """A stream led by the prompt filter chunk, as Azure sends it."""
    stream = oa.chunks(**kw)
    first = Event(
        {
            "choices": [],
            "created": 0,
            "id": "",
            "model": "",
            "object": "",
            "prompt_filter_results": PROMPT_FILTER,
            "usage": None,
        }
    )
    drop = None if stream.drop_after is None else stream.drop_after + 1
    return replace(stream, events=(first, *stream.events), drop_after=drop)


def truth(request_model: str, response: Response, read_events: int | None = None) -> Truth:
    """The OpenAI truth, without the prompt filter chunk: it carries no id, model or count."""
    if response.events is None or response.status >= 400:
        return oa.truth(request_model, response, read_events)
    events = tuple(
        e for e in response.events if not (isinstance(e.data, dict) and e.data.get("id") == "")
    )
    dropped = len(response.events) - len(events)
    stream = replace(
        response,
        events=events,
        drop_after=None if response.drop_after is None else response.drop_after - dropped,
    )
    return oa.truth(
        request_model, stream, None if read_events is None else max(read_events - dropped, 0)
    )
