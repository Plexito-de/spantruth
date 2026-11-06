"""Anthropic Messages stub for round 2. Loopback only. Same JSON body as ../stub.py, plus SSE and cache usage.
Usage: stub.py <port> <hits.jsonl> [cache_read] [cache_write]
A request with "stream": true gets the event sequence that spantruth's wire_anthropic.events() builds:
message_start carries input and cache counts with output_tokens 1, message_delta carries only output_tokens."""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
PORT, LOG = int(sys.argv[1]), sys.argv[2]
READ = int(sys.argv[3]) if len(sys.argv) > 3 else 0
WRITE = int(sys.argv[4]) if len(sys.argv) > 4 else 0
IN, OUT = 1000, 200

def usage(output):
    return {"input_tokens": IN, "output_tokens": output,
            "cache_read_input_tokens": READ, "cache_creation_input_tokens": WRITE}

def sse_events(model):
    start = {"id": "msg_stub01", "type": "message", "role": "assistant", "model": model, "content": [],
             "stop_reason": None, "stop_sequence": None, "usage": usage(1)}
    yield "message_start", {"message": start}
    yield "content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}
    for part in ("po", "ng"):
        yield "content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": part}}
    yield "content_block_stop", {"index": 0}
    yield "message_delta", {"delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": OUT}}
    yield "message_stop", {}

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        stream = bool(req.get("stream"))
        with open(LOG, "a") as f:
            f.write(json.dumps({"port": PORT, "path": self.path, "model": req.get("model"), "stream": stream}) + "\n")
        if stream:
            self.send_response(200); self.send_header("content-type", "text/event-stream")
            self.send_header("request-id", "req_stub"); self.send_header("connection", "close"); self.end_headers()
            for kind, data in sse_events(req.get("model")):
                self.wfile.write(f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n".encode()); self.wfile.flush()
            self.close_connection = True
            return
        body = json.dumps({"id": "msg_stub01", "type": "message", "role": "assistant", "model": req.get("model"),
                           "content": [{"type": "text", "text": "pong"}], "stop_reason": "end_turn", "stop_sequence": None,
                           "usage": usage(OUT)}).encode()
        self.send_response(200); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body))); self.send_header("request-id", "req_stub")
        self.end_headers(); self.wfile.write(body)

ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
