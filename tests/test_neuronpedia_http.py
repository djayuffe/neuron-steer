import http.server
import json
import pathlib
import subprocess
import sys
import threading
import time
import unittest


class NeuronpediaHTTPTests(unittest.TestCase):
    def setUp(self):
        self.paths = []
        paths = self.paths

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                paths.append((self.path, body))
                if self.path.startswith("/slow"):
                    time.sleep(5)
                data = json.dumps([{"id": "synthetic"}]).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        try:
            self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        except PermissionError:
            self.skipTest("sandbox disallows binding a loopback server")
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.endpoint = "http://127.0.0.1:" + str(self.server.server_port)

    def command(self, *flags):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "neuron_steer",
                "neuronpedia",
                "models",
                "--endpoint",
                self.endpoint,
                *flags,
            ],
            cwd=pathlib.Path(__file__).parents[1],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_real_worker_and_loopback_discovery(self):
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["data"], [{"id": "synthetic"}])
        self.assertTrue(payload["network_request_performed"])
        self.assertFalse(payload["model_loaded"])
        self.assertEqual(self.paths, [("/api/model/list", {})])

    def test_hard_timeout_includes_slow_server(self):
        self.endpoint += "/slow"
        started = time.monotonic()
        result = self.command("--timeout", "1")
        self.assertEqual(result.returncode, 2)
        self.assertLess(time.monotonic() - started, 3)
