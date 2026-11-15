"""Print what Opik itself reports, per project. Arg 1: tag prefix. Raw JSON goes to out/opik-<tag>.json."""
import json, sys, urllib.request, urllib.parse
tag = sys.argv[1]; B = "http://127.0.0.1:15173/api/v1/private"
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path, **q):
    return json.load(op.open(f"{B}/{path}?" + urllib.parse.urlencode(q)))
projs = [p for p in get("projects", size=200)["content"] if p["name"].startswith(tag + "-")]
raw = {}
for p in sorted(projs, key=lambda p: p["name"]):
    n = p["name"]
    tr = get("traces", project_name=n, size=50, truncate="true")["content"]
    sp = get("spans", project_name=n, size=50, truncate="true")["content"]
    ts = get("traces/stats", project_name=n)["stats"]; ss = get("spans/stats", project_name=n)["stats"]
    raw[n] = dict(project=p, traces=tr, spans=sp, trace_stats=ts, span_stats=ss)
    pick = lambda st: {s["name"]: s.get("value") for s in st if s["name"] in ("usage.total_tokens","usage.prompt_tokens","usage.completion_tokens","total_estimated_cost","total_estimated_cost_sum","trace_count","span_count","llm_span_count")}
    print(f"\n== {n}: traces={len(tr)} spans={len(sp)}")
    print("   traces/stats", pick(ts)); print("   spans/stats ", pick(ss))
    for t in tr: print(f"   trace usage={t.get('usage')} total_estimated_cost={t.get('total_estimated_cost')} span_count={t.get('span_count')} llm_span_count={t.get('llm_span_count')}")
    for s in sp: print(f"   span {s['name']!r} type={s.get('type')} parent={bool(s.get('parent_span_id'))} model={s.get('model')} provider={s.get('provider')} usage={({k:v for k,v in (s.get('usage') or {}).items() if k in ('prompt_tokens','completion_tokens','total_tokens','input_tokens','output_tokens')})} total_estimated_cost={s.get('total_estimated_cost')}")
json.dump(raw, open(f"out/opik-{tag}.json", "w"), indent=1)
