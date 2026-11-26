"""The scenario pack is complete (each scenario rebuilds from it) and stable (the same bytes)."""

import dataclasses
import http.client
import json

from spantruth import cli, pack, scenarios
from spantruth.mockserver import MockServer
from spantruth.model import Event, Response, Scenario, Truth

ENTRY = {
    *pack.CALL,
    "title",
    "catches",
    "tags",
    "content",
    "depends_on",
    "path",
    "responses",
    "truth_response",
    "truth",
}
# The call-plan keys, in order. A driver in another language reads them, so they never change.
PLAN_KEYS = (
    "id",
    "provider",
    "request_model",
    "stream",
    "stream_usage",
    "tools",
    "read_events",
    "with_block",
    "asynchronous",
    "api",
    "entrypoint",
    "retries",
)


def response_from(data: dict) -> Response:
    events = data.get("events")
    return Response(
        status=data["status"],
        headers=data["headers"],
        body=data.get("body"),
        events=None if events is None else tuple(Event(e["data"], e["event"]) for e in events),
        drop_after=data.get("drop_after"),
    )


def scenario_from(entry: dict) -> Scenario:
    responses = tuple(response_from(r) for r in entry["responses"])
    return Scenario(
        **{k: entry[k] for k in pack.CALL},
        title=entry["title"],
        catches=entry["catches"],
        tags=tuple(entry["tags"]),
        content=entry["content"],
        response=responses[entry["truth_response"]],
        responses=responses,
    )


def truth_from(data: dict) -> Truth:
    """JSON has lists only, so every field that `Truth` declares as a tuple becomes one again."""
    lists = {f.name for f in dataclasses.fields(Truth) if str(f.type).startswith("tuple")}
    return Truth(**{k: tuple(v) if k in lists and v is not None else v for k, v in data.items()})


def exported() -> dict:
    """The pack as a reader in another language gets it: through JSON."""
    return json.loads(pack.dumps(pack.export()))


def test_every_scenario_rebuilds_from_the_pack():
    data = exported()
    assert data["format"] == pack.FORMAT == 1
    assert list(data["scenarios"]) == list(scenarios.SCENARIOS)
    for sid, entry in data["scenarios"].items():
        original = scenarios.get(sid)
        assert scenario_from(entry) == original, sid
        assert truth_from(entry["truth"]) == scenarios.truth(original), sid
        assert entry["depends_on"] == scenarios.depends_on(sid)


def test_every_entry_has_the_same_keys():
    for sid, entry in exported()["scenarios"].items():
        assert set(entry) == ENTRY, sid


def test_the_export_is_the_same_bytes_every_time():
    assert pack.dumps(pack.export()) == pack.dumps(pack.export())
    assert "created" not in pack.export()


def test_the_call_plan_keys_do_not_change():
    assert pack.CALL == PLAN_KEYS
    plans = pack.calls_only()
    assert list(plans) == list(scenarios.SCENARIOS)
    assert all(list(plan) == list(PLAN_KEYS) for plan in plans.values())


def _served(url: str, path: str) -> tuple[int, bytes]:
    """What the mock server sends, up to where it closes the connection."""
    host, port = url.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.request("POST", path + "/x", body=b"{}")
    reply = conn.getresponse()
    try:
        body = reply.read()
    except http.client.IncompleteRead as exc:
        body = exc.partial
    return reply.status, body


def _wire(response: dict) -> bytes:
    """The bytes of a pack response, framed as docs/scenario-pack.md says."""
    if "body" in response:
        return json.dumps(response["body"]).encode()
    out = ""
    for e in response["events"][: response["drop_after"]]:
        data = e["data"] if isinstance(e["data"], str) else json.dumps(e["data"])
        out += (f"event: {e['event']}\n" if e["event"] else "") + f"data: {data}\n\n"
    return out.encode()


def test_the_mock_server_sends_what_the_pack_says():
    with MockServer() as mock:
        for entry in exported()["scenarios"].values():
            mock.reset()
            for response in entry["responses"]:
                status, body = _served(mock.url, entry["path"])
                assert (status, body) == (response["status"], _wire(response)), entry["id"]


def test_export_writes_the_pack_and_the_call_plans(tmp_path, capsys):
    out = tmp_path / "pack.json"
    assert cli.main(["export", "--out", str(out)]) == 0
    assert out.read_text() == pack.dumps(pack.export())
    assert cli.main(["export", "--calls-only"]) == 0
    assert json.loads(capsys.readouterr().out) == pack.calls_only()
