"""Print one table per variant from phoenix-x2.json and opik-x2.json. Usage: summarize.py <phoenix.json> <opik.json> [variant ...]"""
import json, sys
P = json.load(open(sys.argv[1])); O = json.load(open(sys.argv[2])); want = sys.argv[3:]
order = ["A-ll", "A-otel", "A-oi310", "A-oi", "B-ll", "B-otel", "B-oi310", "B-oi", "C-sdk", "B-oilatest", "B-lllatest", "B-otellatest", "C-sdk26"]
def key(n): 
    c = n.split("-", 2)[2]; return order.index(c) if c in order else 99
pp = {r["name"]: r for r in P}
names = sorted(set(pp) | set(O), key=lambda n: (n.split("-")[1], key(n)))
cur = None
for n in names:
    v = n.split("-")[1]
    if want and v not in want: continue
    if v != cur:
        cur = v; print(f"\n### {v}")
    if n in pp:
        r = pp[n]; sp = [e["node"] for e in r["spans"]["edges"]]
        print(f"{n} PHOENIX traceCount={r['traceCount']} recordCount={r['recordCount']} tokenCountTotal={r['tokenCountTotal']} ({r['tokenCountPrompt']}/{r['tokenCountCompletion']}) costSummary.total={r['costSummary']['total']['cost']}")
        for s in sp:
            d = {e['tokenType']: round(e['value']['cost'] or 0, 7) for e in s['costDetailSummaryEntries']}; pd = s['tokenPromptDetails'] or {}
            print(f"    {s['name']:28} {s['spanKind']:8} parent={bool(s['parentId'])} trace={s['trace']['traceId'][:6]} tok={s['tokenCountTotal']}({s['tokenCountPrompt']}/{s['tokenCountCompletion']}) cache r/w={pd.get('cacheRead')}/{pd.get('cacheWrite')} cum={s['cumulativeTokenCountTotal']} cost={((s['costSummary'] or {}).get('total') or {}).get('cost')} detail={d} traceCost={s['trace']['costSummary']['total']['cost']}")
    if n in O:
        r = O[n]; ps = r.get('project_stats') or {}
        tsum = lambda k: sum((t.get('usage') or {}).get(k, 0) for t in r['traces'])
        print(f"{n} OPIK traces={len(r['traces'])} spans={len(r['spans'])} sum(trace.usage.total_tokens)={tsum('total_tokens')} sum(trace.cost)={round(sum(t.get('total_estimated_cost') or 0 for t in r['traces']),7)} project_stats: cost_sum={ps.get('total_estimated_cost_sum')} cost_avg={ps.get('total_estimated_cost')} usage_total_avg={(ps.get('usage') or {}).get('total_tokens')}")
        for t in r['traces']:
            print(f"    TRACE {t['name'][:28]:28} usage={ {k:v for k,v in (t.get('usage') or {}).items() if not k.startswith('original')} } cost={t.get('total_estimated_cost')} spans={t.get('span_count')} llm={t.get('llm_span_count')}")
        for s in r['spans']:
            print(f"    span  {s['name'][:28]:28} type={s.get('type')} parent={bool(s.get('parent_span_id'))} model={s.get('model')} prov={s.get('provider')} usage={ {k:v for k,v in (s.get('usage') or {}).items() if not k.startswith('original')} } cost={s.get('total_estimated_cost')}")
