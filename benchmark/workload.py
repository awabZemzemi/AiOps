"""Small functional HTTP workload with bounded work and explicit errors."""
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/health", "/ready"):
            return self.reply(200, {"ok": True})
        if self.path != "/functional":
            return self.reply(404, {"error": "unknown path"})
        started = time.monotonic()
        try:
            delay = min(float(os.getenv("UPSTREAM_DELAY", "0")), 2.0)
            if delay:
                time.sleep(delay)
            template = os.getenv("TEMPLATE_FILE")
            if template:
                with open(template, encoding="utf-8") as stream:
                    stream.read(1024)
            output = os.getenv("WRITE_PATH")
            if output:
                with open(output, "w", encoding="utf-8") as stream:
                    stream.write("bounded test record\n")
            count = min(int(os.getenv("WORK_ITERATIONS", "0")), 4_000_000)
            total = 0
            for n in range(count):
                total += n * n % 17
            dependency = os.getenv("DEPENDENCY_URL")
            if dependency:
                timeout = min(float(os.getenv("DEPENDENCY_TIMEOUT", "1")), 3.0)
                with urllib.request.urlopen(dependency, timeout=timeout) as response:
                    response.read(1024)
            return self.reply(200, {"ok": True, "elapsed_ms": round((time.monotonic() - started) * 1000), "total": total})
        except Exception as error:
            print(f"functional request failed: {type(error).__name__}: {error}", flush=True)
            return self.reply(500, {"ok": False, "error_type": type(error).__name__})

    def reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(os.getenv("PORT", "8080"))
    # A single-request server bounds application-side concurrency without a
    # background worker pool or unbounded request threads.
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
