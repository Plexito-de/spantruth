"""Save what Opik reports for every project whose name starts with <tag>-. Usage: read_opik.py <tag> <out.json>
Saves traces, spans, the trace and span stats, and the row of /projects/stats, which holds the project-level values."""
import json, sys, urllib.request, urllib.parse
tag, out = sys.argv[1:3]; B = "http://127.0.0.1:15173/api/v1/private"
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path, **q):
    return json.load(op.open(f"{B}/{path}?" + urllib.parse.urlencode(q)))
projs = [p for p in get("projects", size=1000)["content"] if p["name"].startswith(tag + "-")]
stats = {s["project_id"]: s for s in get("projects/stats", size=1000)["content"]}
raw = {}
for p in sorted(projs, key=lambda p: p["name"]):
    n = p["name"]
    raw[n] = dict(project=p, project_stats=stats.get(p["id"]),
                  traces=get("traces", project_name=n, size=50, truncate="true")["content"],
                  spans=get("spans", project_name=n, size=50, truncate="true")["content"],
                  trace_stats=get("traces/stats", project_name=n)["stats"], span_stats=get("spans/stats", project_name=n)["stats"])
json.dump(raw, open(out, "w"), indent=1)
print(len(raw), "projects written to", out)
