"""One-route Anthropic Messages stub. Always returns the same usage: 1000 in, 200 out. Logs each hit."""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
LOG = sys.argv[2]
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        with open(LOG, "a") as f:
            f.write(json.dumps({"path": self.path, "model": req.get("model")}) + "\n")
        body = json.dumps({
            "id": "msg_stub01", "type": "message", "role": "assistant", "model": req.get("model"),
            "content": [{"type": "text", "text": "pong"}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1000, "output_tokens": 200,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        }).encode()
        self.send_response(200); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body))); self.send_header("request-id", "req_stub")
        self.end_headers(); self.wfile.write(body)
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
