"""Save what Phoenix reports for every project whose name starts with <tag>-. Usage: read_phoenix.py <tag> <out.json>"""
import json, sys, urllib.request
tag, out = sys.argv[1:3]
COST = "tokenType isPrompt value { cost tokens }"
Q = """{ projects(first: 1000) { edges { node { name traceCount recordCount
  tokenCountTotal tokenCountPrompt tokenCountCompletion
  costSummary { total {cost tokens} prompt {cost tokens} completion {cost tokens} }
  spans(first: 50) { edges { node {
    name spanKind parentId statusCode tokenCountTotal tokenCountPrompt tokenCountCompletion
    tokenPromptDetails { cacheRead cacheWrite audio }
    cumulativeTokenCountTotal cumulativeTokenCountPrompt cumulativeTokenCountCompletion
    costSummary { total {cost tokens} prompt {cost tokens} completion {cost tokens} }
    costDetailSummaryEntries { %s }
    trace { traceId numSpans costSummary { total {cost tokens} prompt {cost tokens} completion {cost tokens} } costDetailSummaryEntries { %s } }
  } } }
} } } }""" % (COST, COST)
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def gql(q):
    return json.load(op.open(urllib.request.Request("http://127.0.0.1:16006/graphql", json.dumps({"query": q}).encode(), {"content-type": "application/json"})))
d = gql(Q)
if "errors" in d:
    print(json.dumps(d["errors"], indent=1)); sys.exit(1)
rows = sorted((e["node"] for e in d["data"]["projects"]["edges"] if e["node"]["name"].startswith(tag + "-")), key=lambda r: r["name"])
json.dump(rows, open(out, "w"), indent=1)
print(len(rows), "projects written to", out)
