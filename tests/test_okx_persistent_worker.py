import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.research_lab.providers.okx_public import PersistentHttpGet


class _Handler(BaseHTTPRequestHandler):
    calls = 0

    def do_GET(self):  # noqa: N802 - stdlib callback name
        type(self).calls += 1
        payload = json.dumps({"code": "0", "data": [[str(type(self).calls)] ]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


def test_persistent_worker_reuses_a_killable_child():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    getter = PersistentHttpGet()
    try:
        url = f"http://127.0.0.1:{server.server_port}/candles"
        assert getter(url, 2.0)["code"] == "0"
        first_pid = getter._process.pid
        assert getter(url, 2.0)["code"] == "0"
        assert getter._process.pid == first_pid
        assert _Handler.calls == 2
    finally:
        getter.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    test_persistent_worker_reuses_a_killable_child()
    print("PERSISTENT_HTTP_WORKER_OK")
