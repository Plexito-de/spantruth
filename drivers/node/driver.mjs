#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

import { trace } from "@opentelemetry/api";
import { BasicTracerProvider, SimpleSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";
import { Resource } from "@opentelemetry/resources";
import { SEMRESATTRS_SERVICE_NAME } from "@opentelemetry/semantic-conventions";

import { Seen } from "./seen.mjs";
import { run } from "./calls.mjs";
import { installProbe } from "./probe.mjs";

const HANG_MS = 20000;

function loadScenariosRegistry() {
  const file = path.join(path.dirname(new URL(import.meta.url).pathname), "scenarios.json");
  if (fs.existsSync(file)) {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  }
  return {};
}

function resolvePlan(scenarioId, planArg, registry) {
  if (planArg) {
    if (fs.existsSync(planArg)) {
      const data = JSON.parse(fs.readFileSync(planArg, "utf8"));
      return data[scenarioId] || data;
    }
    try {
      const parsed = JSON.parse(planArg);
      return parsed[scenarioId] || parsed;
    } catch {
      // planArg is not raw json, fall back
    }
  }
  if (registry[scenarioId]) {
    return registry[scenarioId];
  }
  throw new Error(`unknown scenario: ${scenarioId}`);
}

function writeJson(file, data) {
  fs.writeFileSync(file, JSON.stringify(data, null, 2) + "\n", "utf8");
}

function startWatchdog(outFile, scenarioId) {
  const timer = setTimeout(() => {
    writeJson(outFile, { scenario: scenarioId, hang: true });
    process.exit(3);
  }, HANG_MS);
  timer.unref();
  return timer;
}

export async function observe(plan, mockUrl) {
  const seen = new Seen();
  const start = Number(process.hrtime.bigint());
  let exception = null;
  const key = plan.id.replace(/\//g, "--");
  const baseUrl = `${mockUrl.replace(/\/$/, "")}/s/${key}/v1`;

  try {
    await run(plan, baseUrl, seen);
  } catch (err) {
    const mod = plan.provider;
    const clsName = err?.constructor?.name || err?.name || "Error";
    exception = `${mod}.${clsName}`;
  }

  return {
    scenario: plan.id,
    exception,
    seen: seen.asDict(),
    start_ns: start,
    end_ns: Number(process.hrtime.bigint()),
  };
}

async function runBaseline(scenarioIds, mockUrl, outFile, planArg, registry) {
  const results = {};
  for (const sid of scenarioIds) {
    const plan = resolvePlan(sid, planArg, registry);
    results[sid] = await observe(plan, mockUrl);
  }
  writeJson(outFile, results);
  return 0;
}

async function runInstrumented(scenarioId, mockUrl, otlpUrl, subjectDir, outFile, planArg, registry) {
  const plan = resolvePlan(scenarioId, planArg, registry);
  const { spans, processor } = installProbe();
  const provider = new BasicTracerProvider({
    resource: new Resource({ [SEMRESATTRS_SERVICE_NAME]: "spantruth" }),
  });
  provider.addSpanProcessor(processor);
  provider.addSpanProcessor(
    new SimpleSpanProcessor(
      new OTLPTraceExporter({ url: `${otlpUrl.replace(/\/$/, "")}/v1/traces` })
    )
  );
  provider.register();

  const result = { scenario: scenarioId, hang: false };
  let extra = [];

  try {
    const mjsPath = path.resolve(subjectDir, "instrument.mjs");
    const jsPath = path.resolve(subjectDir, "instrument.js");
    const target = fs.existsSync(mjsPath) ? mjsPath : jsPath;
    if (fs.existsSync(target)) {
      const subjectMod = await import(pathToFileURL(target).href);
      if (typeof subjectMod.instrument === "function") {
        extra = await subjectMod.instrument({ tracerProvider: provider, otlpEndpoint: otlpUrl });
      }
    }
  } catch (err) {
    result.instrument_error = err?.stack || String(err);
    writeJson(outFile, result);
    return 2;
  }

  const timer = startWatchdog(outFile, scenarioId);
  const tracer = trace.getTracer("spantruth.driver");

  await tracer.startActiveSpan("spantruth.scenario", async (parentSpan) => {
    const ctx = parentSpan.spanContext();
    const obs = await observe(plan, mockUrl);
    Object.assign(result, obs);
    result.parent = {
      trace_id: ctx.traceId,
      span_id: ctx.spanId,
    };
    parentSpan.end();
  });

  clearTimeout(timer);
  result.probe = { ...spans };

  await provider.forceFlush();
  if (Array.isArray(extra)) {
    for (const p of extra) {
      if (p && typeof p.forceFlush === "function") {
        await p.forceFlush();
      }
    }
  }

  writeJson(outFile, result);
  return 0;
}

function parseArgs(args) {
  const parsed = {
    mode: null,
    scenarios: [],
    mock: null,
    out: null,
    otlp: null,
    subject: null,
    plan: null,
  };

  let i = 0;
  if (args.length > 0 && !args[0].startsWith("-")) {
    parsed.mode = args[0];
    i = 1;
  }

  while (i < args.length) {
    const arg = args[i];
    if (arg === "--mock") {
      parsed.mock = args[++i];
    } else if (arg === "--out") {
      parsed.out = args[++i];
    } else if (arg === "--otlp") {
      parsed.otlp = args[++i];
    } else if (arg === "--subject") {
      parsed.subject = args[++i];
    } else if (arg === "--plan") {
      parsed.plan = args[++i];
    } else if (!arg.startsWith("-")) {
      parsed.scenarios.push(arg);
    }
    i++;
  }

  return parsed;
}

export async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  const registry = loadScenariosRegistry();

  if (args.mode === "baseline") {
    if (!args.mock || !args.out || args.scenarios.length === 0) {
      console.error("Usage: driver.mjs baseline <scenarios...> --mock <url> --out <file>");
      return 1;
    }
    return runBaseline(args.scenarios, args.mock, args.out, args.plan, registry);
  }

  if (args.mode === "instrumented") {
    if (!args.mock || !args.out || !args.otlp || !args.subject || args.scenarios.length !== 1) {
      console.error("Usage: driver.mjs instrumented <scenario> --mock <url> --otlp <url> --subject <dir> --out <file>");
      return 1;
    }
    return runInstrumented(args.scenarios[0], args.mock, args.otlp, args.subject, args.out, args.plan, registry);
  }

  console.error("Unknown mode:", args.mode);
  return 1;
}

if (process.argv[1] && import.meta.url.endsWith(path.basename(process.argv[1]))) {
  main().then((code) => {
    if (code !== 0) {
      process.exit(code);
    }
  });
}
