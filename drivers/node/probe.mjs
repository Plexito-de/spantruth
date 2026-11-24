/**
 * Record every SDK span's start and end in this process, whichever library made it.
 *
 * A span that never ends is never exported, so the receiver alone cannot tell a span that
 * never ended from one that was never started. The probe can.
 */

export class ProbeSpanProcessor {
  constructor(spansRecord) {
    this.spans = spansRecord;
  }

  onStart(span) {
    try {
      const ctx = typeof span.spanContext === "function" ? span.spanContext() : null;
      const sid = ctx ? ctx.spanId : null;
      if (sid) {
        this.spans[sid] = {
          name: span.name,
          start_ns: Number(process.hrtime.bigint()),
          end_ns: null,
        };
      }
    } catch {
      // Ignore errors in probe recording
    }
  }

  onEnd(span) {
    try {
      const ctx = typeof span.spanContext === "function" ? span.spanContext() : null;
      const sid = ctx ? ctx.spanId : null;
      if (sid && this.spans[sid] && this.spans[sid].end_ns === null) {
        this.spans[sid].end_ns = Number(process.hrtime.bigint());
      }
    } catch {
      // Ignore errors in probe recording
    }
  }

  shutdown() {
    return Promise.resolve();
  }

  forceFlush() {
    return Promise.resolve();
  }
}

export function installProbe() {
  const spans = {};
  const processor = new ProbeSpanProcessor(spans);
  return { spans, processor };
}

