// The parsers and the lookup of docs/site/check.js. Run: node --test tests/js/
// tests/test_check_page.py runs this file under pytest, so CI runs it.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  LockError,
  byClass,
  byScenario,
  check,
  compareVersions,
  detect,
  findingId,
  lookup,
  match,
  parseLock,
  versionKey,
} from "../../docs/site/check.js";

const lock = (name) => readFileSync(new URL(`./locks/${name}`, import.meta.url), "utf8");
const obj = (map) => Object.fromEntries(map);

test("uv.lock: every [[package]] name and version, and none from a sub-table", () => {
  const { source, versions } = parseLock(lock("uv.lock"), "uv.lock");
  assert.equal(source, "uv.lock");
  assert.deepEqual(obj(versions), {
    anthropic: "2.4.0",
    "openinference-instrumentation-anthropic": "3.2.0",
    "opentelemetry-sdk": "1.45.2",
    "my-app": "0.1.0",
    "local-thing": null,
  });
});

test("poetry.lock and pdm.lock read the same way", () => {
  assert.deepEqual(obj(parseLock(lock("poetry.lock")).versions), {
    openai: "3.42.1",
    "traceloop-sdk": "0.62.4",
    "opentelemetry-instrumentation-openai": "0.62.4",
  });
  assert.deepEqual(obj(parseLock(lock("pdm.lock")).versions), { logfire: "5.4.0", openai: "3.42.1" });
});

test("Pipfile.lock: default and develop, with the == taken off", () => {
  assert.deepEqual(obj(parseLock(lock("Pipfile.lock")).versions), {
    "sentry-sdk": "2.74.0",
    anthropic: "2.4.0",
    "editable-thing": null,
    pytest: "9.0.0",
  });
});

test("requirements.txt: pins with hashes, extras and markers; options and comments skipped", () => {
  assert.deepEqual(obj(parseLock(lock("requirements.txt")).versions), {
    openai: "3.42.1",
    "mlflow-skinny": "3.18.0",
    ddtrace: null,
    langfuse: "5.2.0",
  });
});

test("the kind of a pasted file comes from its content", () => {
  assert.equal(detect(lock("uv.lock")), "uv.lock");
  assert.equal(detect(lock("poetry.lock")), "poetry.lock");
  assert.equal(detect(lock("pdm.lock")), "pdm.lock");
  assert.equal(detect(lock("Pipfile.lock")), "Pipfile.lock");
  assert.equal(detect(lock("requirements.txt")), "requirements.txt");
  assert.equal(detect("x", "requirements-dev.txt"), "requirements.txt");
});

test("a broken Pipfile.lock and a huge file are errors, not results", () => {
  assert.throws(() => parseLock("{ not json", "Pipfile.lock"), LockError);
  assert.throws(() => parseLock("a".repeat(5_000_001)), LockError);
});

test("versions sort by release, as history.version_key does", () => {
  const ordered = ["0.9", "1.0.dev1", "1.0a1", "1.0b2", "1.0rc1", "1.0", "1.0.post1", "1.2", "1.10"];
  const shuffled = [...ordered].reverse();
  assert.deepEqual(shuffled.sort(compareVersions), ordered);
  assert.equal(compareVersions("1.0", "1.0.0"), 0);
  assert.equal(versionKey("not a version"), null);
});

test("a finding anchor is the one report.finding_id writes", () => {
  assert.equal(
    findingId("openinference", "anthropic/stream-early-exit", "span.ended"),
    "openinference--anthropic--stream-early-exit--span.ended",
  );
});

const TABLE = {
  schema: 1,
  created: "2026-11-26T03:00:00+00:00",
  spantruth: "0.5.0",
  releases: 3,
  subjects: {
    openinference: {
      title: "OpenInference",
      providers: ["openai", "anthropic"],
      detect: ["openinference-instrumentation-openai", "openinference-instrumentation-anthropic"],
      detect_with: [],
      packages: {
        "openinference-instrumentation-anthropic": {
          pinned: "3.3.0",
          scenarios: ["anthropic/basic", "anthropic/stream-early-exit"],
          versions: [
            { version: "3.1.0", run: "2026-11-26", released: "2026-09-01", failing: { "anthropic/stream-early-exit": { "span.ended": "ST01" } }, unknown: [], error: "" },
            { version: "3.2.0", run: "2026-11-26", released: "2026-10-01", failing: { "anthropic/stream-early-exit": { "span.ended": "ST01" } }, unknown: [], error: "" },
            { version: "3.3.0", run: "2026-11-26", released: "2026-11-01", failing: {}, unknown: [], error: "" },
          ],
          first_fixed: [{ scenario: "anthropic/stream-early-exit", check: "span.ended", bug: "ST01", version: "3.3.0" }],
        },
      },
    },
    ddtrace: {
      title: "ddtrace",
      providers: ["openai", "anthropic"],
      detect: ["ddtrace"],
      detect_with: ["openai", "anthropic"],
      packages: { ddtrace: { pinned: "4.16.3", scenarios: [], versions: [], first_fixed: [] } },
    },
    "anthropic-sdk": {
      title: "Anthropic SDK",
      providers: ["anthropic"],
      detect: [],
      detect_with: [],
      packages: { anthropic: { pinned: "2.4.0", scenarios: [], versions: [], first_fixed: [] } },
    },
  },
};

test("match follows doctor: detect_with needs an SDK, and the SDK alone counts with an OTel SDK", () => {
  assert.deepEqual(match(TABLE, new Map([["ddtrace", "4.0"]])), []);
  assert.deepEqual(match(TABLE, new Map([["ddtrace", "4.0"], ["openai", "3"]])), ["ddtrace"]);
  assert.deepEqual(match(TABLE, new Map([["anthropic", "2"], ["opentelemetry-sdk", "1"]])), ["anthropic-sdk"]);
  const both = new Map([["anthropic", "2"], ["opentelemetry-sdk", "1"], ["openinference-instrumentation-anthropic", "3.2.0"]]);
  assert.deepEqual(match(TABLE, both), ["openinference"]);
});

test("a measured version lists its failing checks and the release that fixes each", () => {
  const row = lookup(TABLE, "openinference", "openinference-instrumentation-anthropic", "3.2.0");
  assert.equal(row.state, "measured");
  assert.deepEqual(row.failing, [
    { scenario: "anthropic/stream-early-exit", check: "span.ended", bug: "ST01", fixedIn: "3.3.0" },
  ]);
  assert.deepEqual(lookup(TABLE, "openinference", "openinference-instrumentation-anthropic", "3.3").failing, []);
});

test("a version outside the table says where it is, and an unpinned one says so", () => {
  const at = (v) => lookup(TABLE, "openinference", "openinference-instrumentation-anthropic", v).state;
  assert.equal(at("3.0.0"), "older");
  assert.equal(at("3.4.0"), "newer");
  assert.equal(at("3.2.5"), "between");
  assert.equal(at(null), "unpinned");
  assert.equal(at(undefined), "missing");
});

test("check puts it together from the parsed file", () => {
  const result = check(TABLE, parseLock(lock("uv.lock"), "uv.lock"));
  assert.equal(result.source, "uv.lock");
  assert.deepEqual(result.subjects.map((s) => s.name), ["openinference"]);
  assert.equal(result.subjects[0].packages[0].state, "measured");
  assert.deepEqual(result.sdks, [{ name: "anthropic", version: "2.4.0" }]);
});

test("the failing checks group by bug class, with the release that fixes all or part of them", () => {
  const failing = [
    { scenario: "a/x", check: "span.ended", bug: "ST01", fixedIn: "1.10" },
    { scenario: "a/y", check: "span.ended", bug: "ST01", fixedIn: "1.9" },
    { scenario: "a/y", check: "input_tokens", bug: "ST03", fixedIn: "1.9" },
    { scenario: "a/z", check: "input_tokens", bug: "ST03", fixedIn: "" },
    { scenario: "a/z", check: "response_model", bug: "", fixedIn: "" },
  ];
  assert.deepEqual(byClass(failing), [
    { bug: "ST01", scenarios: 2, fixedIn: "1.10", unfixed: 0 },
    { bug: "ST03", scenarios: 2, fixedIn: "1.9", unfixed: 1 },
    { bug: "", scenarios: 1, fixedIn: "", unfixed: 1 },
  ]);
  assert.deepEqual(
    byScenario(failing).map((s) => [s.scenario, s.checks.length]),
    [["a/x", 1], ["a/y", 2], ["a/z", 2]],
  );
});
