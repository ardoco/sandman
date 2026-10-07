"""Logging HTTP proxy: python3 wiretap.py LISTEN_PORT TARGET_BASE_URL. Prints every request/response body (wire JSON)."""

import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, TARGET = int(sys.argv[1]), sys.argv[2].rstrip("/")


class Tap(BaseHTTPRequestHandler):
    def _forward(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length", "connection", "upgrade", "http2-settings")}
        req = urllib.request.Request(TARGET + self.path, data=body, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req) as r:
                status, rheaders, rbody = r.status, r.getheaders(), r.read()
        except urllib.error.HTTPError as e:
            status, rheaders, rbody = e.code, e.headers.items(), e.read()
        auth = "auth" if self.headers.get("Authorization") else "no-auth"
        print(f">>> {self.command} {self.path} ({auth}) {(body or b'').decode()}\n<<< {status} {rbody.decode()}\n", flush=True)
        self.send_response(status)
        for k, v in rheaders:
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(rbody)))
        self.end_headers()
        self.wfile.write(rbody)

    do_GET = do_POST = _forward

    def log_message(self, *args: object) -> None:
        pass


ThreadingHTTPServer(("127.0.0.1", PORT), Tap).serve_forever()
