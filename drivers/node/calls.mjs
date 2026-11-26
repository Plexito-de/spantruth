import OpenAI from "openai";
import Anthropic from "@anthropic-ai/sdk";

export const PROMPT = [{ role: "user", content: "What is the capital of France?" }];

export const OPENAI_TOOLS = [
  {
    type: "function",
    function: {
      name: "get_weather",
      parameters: { type: "object", properties: { city: { type: "string" } } },
    },
  },
  {
    type: "function",
    function: {
      name: "get_time",
      parameters: { type: "object", properties: { zone: { type: "string" } } },
    },
  },
];

export const ANTHROPIC_TOOLS = [
  {
    name: "get_weather",
    input_schema: { type: "object", properties: { city: { type: "string" } } },
  },
  {
    name: "get_time",
    input_schema: { type: "object", properties: { zone: { type: "string" } } },
  },
];

function openaiKwargs(plan) {
  const kw = { model: plan.request_model, messages: PROMPT };
  if (plan.tools) {
    kw.tools = OPENAI_TOOLS;
  }
  if (plan.stream) {
    kw.stream = true;
  }
  if (plan.stream && plan.stream_usage) {
    kw.stream_options = { include_usage: true };
  }
  return kw;
}

function anthropicKwargs(plan) {
  const kw = { model: plan.request_model, max_tokens: 1024, messages: PROMPT };
  if (plan.tools) {
    kw.tools = ANTHROPIC_TOOLS;
  }
  if (plan.stream) {
    kw.stream = true;
  }
  return kw;
}

function openaiResult(r, seen) {
  const msg = r.choices[0].message;
  seen.text = (msg.content || "") + (msg.tool_calls || []).map((t) => t.function.name).join("");
  seen.response_id = r.id;
  seen.usage = r.usage ? JSON.parse(JSON.stringify(r.usage)) : null;
}

function openaiChunk(c, seen) {
  seen.events += 1;
  seen.response_id = c.id;
  if (c.choices && c.choices[0] && c.choices[0].delta && c.choices[0].delta.content) {
    seen.text += c.choices[0].delta.content;
  }
  if (c.choices && c.choices[0] && c.choices[0].delta && c.choices[0].delta.tool_calls) {
    seen.text += c.choices[0].delta.tool_calls
      .filter((tc) => tc.function && tc.function.name)
      .map((tc) => tc.function.name)
      .join("");
  }
  if (c.usage) {
    seen.usage = JSON.parse(JSON.stringify(c.usage));
  }
}

function responsesEvent(e, seen) {
  seen.events += 1;
  if (e.type === "response.created") {
    seen.response_id = e.response.id;
  } else if (e.type === "response.output_text.delta") {
    seen.text += e.delta;
  } else if (e.type === "response.completed" && e.response.usage) {
    seen.usage = JSON.parse(JSON.stringify(e.response.usage));
  }
}

function anthropicResult(r, seen) {
  seen.text = r.content
    .map((b) => (b.type === "text" ? b.text : b.type === "tool_use" ? b.name : ""))
    .join("");
  seen.response_id = r.id;
  seen.usage = r.usage ? JSON.parse(JSON.stringify(r.usage)) : null;
}

function anthropicEvent(e, seen) {
  seen.events += 1;
  if (e.type === "message_start") {
    seen.response_id = e.message.id;
  } else if (e.type === "content_block_delta" && e.delta && e.delta.type === "text_delta") {
    seen.text += e.delta.text;
  }
}

async function readStream(stream, plan, seen, onEvent) {
  try {
    for await (const event of stream) {
      onEvent(event, seen);
      if (plan.read_events != null && seen.events >= plan.read_events) {
        break;
      }
    }
  } finally {
    if (typeof stream.abort === "function") {
      stream.abort();
    } else if (stream.controller && typeof stream.controller.abort === "function") {
      stream.controller.abort();
    }
  }
}

export async function run(plan, baseUrl, seen) {
  if (plan.provider === "openai") {
    const client = new OpenAI({
      baseURL: baseUrl,
      apiKey: "wt-test",
      maxRetries: plan.retries ?? 0,
      timeout: 10000,
    });

    if (plan.api === "responses") {
      const kw = { model: plan.request_model, input: PROMPT, stream: plan.stream };
      const stream = await client.responses.create(kw);
      if (plan.stream) {
        await readStream(stream, plan, seen, responsesEvent);
      }
      return;
    }

    const kw = openaiKwargs(plan);
    if (plan.entrypoint === "with_streaming_response") {
      // In OpenAI JS SDK, withResponse() gives the raw response with streaming headers
      const stream = await client.chat.completions.create(kw, {
        headers: { "X-Stainless-Raw-Response": "stream" },
      });
      await readStream(stream, plan, seen, openaiChunk);
      return;
    }

    if (!plan.stream) {
      const result = await client.chat.completions.create(kw);
      openaiResult(result, seen);
      return;
    }

    const stream = await client.chat.completions.create(kw);
    await readStream(stream, plan, seen, openaiChunk);
  } else {
    const client = new Anthropic({
      baseURL: baseUrl,
      apiKey: "wt-test",
      maxRetries: plan.retries ?? 0,
      timeout: 10000,
    });

    const kw = anthropicKwargs(plan);
    if (!plan.stream) {
      const result = await client.messages.create(kw);
      anthropicResult(result, seen);
      return;
    }

    const stream = await client.messages.create(kw);
    await readStream(stream, plan, seen, anthropicEvent);
  }
}

