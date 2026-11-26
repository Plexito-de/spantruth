# The results document schema

[`results.schema.json`](results.schema.json) is a JSON Schema (draft 2020-12) for the document that
`spantruth run` and `spantruth doctor` write with `--out`. It describes schema version 1, the value
of the document's `schema` key. Documents written before spantruth 0.3.0 have no `schema` key. They
have the version 1 shape, and the schema accepts them.

Validate a document with any JSON Schema tool, for example:

```sh
uvx check-jsonschema --schemafile schema/results.schema.json results.json
```

`tests/test_schema.py` validates every results document under `results/` and a fresh run of both
controls against it.

## What a breaking change is

A program that reads a results document must keep working when spantruth adds to it. So:

- **Not breaking**: a new key anywhere, a new scenario ID, a new check ID, a new subject. The
  schema allows keys that it does not name, and a reader must ignore keys that it does not know.
  A new optional key goes into the schema in the same pull request, with the version that added it.
- **Breaking**: a key that goes away or is renamed, a key that changes its type (for example a
  string that becomes a list), a key that changes its meaning, a key that becomes required, or a
  new value in a closed list such as the verdicts or `mode`.

A breaking change raises `SCHEMA` in `src/spantruth/runner.py`, adds a new schema file for the new
version, and keeps this one, so documents of both versions stay readable. The changelog names the
change under **Changed**. A check ID and a scenario ID never change meaning, so they never need a
new version: a new meaning gets a new ID.
