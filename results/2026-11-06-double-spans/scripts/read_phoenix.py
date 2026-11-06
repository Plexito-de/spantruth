"""Print what Phoenix itself reports, per project. Arg 1: tag prefix. Raw JSON goes to out/phoenix-<tag>.json."""
import json, sys, time, urllib.request
tag = sys.argv[1]
Q = """{ projects(first: 100) { edges { node { name traceCount recordCount
  tokenCountTotal tokenCountPrompt tokenCountCompletion
  costSummary { total {cost tokens} prompt {cost tokens} completion {cost tokens} }
  spans(first: 50) { edges { node {
    name spanKind parentId tokenCountTotal tokenCountPrompt tokenCountCompletion
    cumulativeTokenCountTotal cumulativeTokenCountPrompt cumulativeTokenCountCompletion
    costSummary { total {cost tokens} }
    trace { traceId costSummary { total {cost tokens} } }
  } } }
} } } }"""
def gql(q):
    req = urllib.request.Request("http://127.0.0.1:16006/graphql", json.dumps({"query": q}).encode(), {"content-type": "application/json"})
    o = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.load(o.open(req))
d = gql(Q)
if "errors" in d: print(json.dumps(d["errors"], indent=1)); sys.exit(1)
rows = [e["node"] for e in d["data"]["projects"]["edges"] if e["node"]["name"].startswith(tag + "-")]
json.dump(rows, open(f"out/phoenix-{tag}.json", "w"), indent=1)
for p in sorted(rows, key=lambda r: r["name"]):
    print(f'\n== {p["name"]}: traceCount={p["traceCount"]} recordCount={p["recordCount"]} tokenCountTotal={p["tokenCountTotal"]} tokenCountPrompt={p["tokenCountPrompt"]} tokenCountCompletion={p["tokenCountCompletion"]} costSummary.total={p["costSummary"]["total"]}')
    for e in p["spans"]["edges"]:
        s = e["node"]
        print(f'   span {s["name"]!r} kind={s["spanKind"]} parent={bool(s["parentId"])} tokenCountTotal={s["tokenCountTotal"]} ({s["tokenCountPrompt"]}/{s["tokenCountCompletion"]}) cumulativeTokenCountTotal={s["cumulativeTokenCountTotal"]} cost={s["costSummary"]["total"]} traceCost={s["trace"]["costSummary"]["total"]}')
