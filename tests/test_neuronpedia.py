import contextlib
import io
import json
import os
import time
import unittest
from unittest import mock

from neuron_steer import neuronpedia as np
from neuron_steer.cli import main, parser
from neuron_steer.steering import NeuronSteerError

URL = "https://www.neuronpedia.org/gemma-2-2b/20-gemmascope-res-16k/10956"


class NeuronpediaTests(unittest.TestCase):
    def args(self, action, *flags):
        return parser().parse_args(["neuronpedia", action, *flags])

    def test_all_payloads(self):
        cases = [
            ("models", [], None, "/api/model/list"),
            ("model", ["--model", "gpt2-small"], None, "/api/model/get"),
            (
                "source",
                ["--model", "gpt2-small", "--source-set", "res-jb"],
                None,
                "/api/source-set/gpt2-small/res-jb",
            ),
            (
                "feature",
                ["--url", URL],
                None,
                "/api/feature/gemma-2-2b/20-gemmascope-res-16k/10956",
            ),
            (
                "search",
                ["--model", "gpt2-small", "--source", "6-res-jb", "--stdin"],
                "synthetic",
                "/api/explanation/search",
            ),
            ("activate", ["--url", URL, "--stdin"], "synthetic", "/api/activation/new"),
            (
                "top",
                ["--model", "gpt2-small", "--source-set", "res-jb", "--stdin"],
                "synthetic",
                "/api/search-all",
            ),
            ("steer", ["--url", URL, "--stdin"], "synthetic", "/api/steer"),
        ]
        for action, flags, text, path in cases:
            with self.subTest(action=action):
                self.assertEqual(
                    np.build_request(self.args(action, *flags), text)[0], path
                )
        _, body = np.build_request(
            self.args("steer", "--url", URL, "--stdin", "--strength", "-2"), "synthetic"
        )
        self.assertEqual(body["features"][0]["strength"], -2)
        self.assertFalse(body["stream"])
        _, body = np.build_request(
            self.args("activate", "--url", URL, "--stdin"), "synthetic"
        )
        self.assertEqual(body["feature"]["index"], "10956")

    def test_feature_urls_and_references(self):
        self.assertEqual(np.parse_feature_url(URL)["index"], 10956)
        self.assertEqual(np.normalize_references([URL, URL.replace("www.", "")]), [URL])
        for url in [
            "http://www.neuronpedia.org/a/b/1",
            URL + "?x=1",
            URL + "#x",
            URL.replace("www.neuronpedia.org", "evil.test"),
            URL.replace("20-gemmascope-res-16k", ".."),
        ]:
            with self.subTest(url=url), self.assertRaises(NeuronSteerError):
                np.parse_feature_url(url)
        with self.assertRaises(NeuronSteerError):
            np.normalize_references([URL] * 17)

    def test_endpoint_boundary(self):
        for endpoint in [
            "http://localhost:3000",
            "http://127.0.0.1:3000/base",
            "https://[::1]:4443",
            np.ORIGIN,
        ]:
            np.endpoint_parts(endpoint)
        self.assertEqual(np.endpoint_parts("http://localhost:3000")[2], "127.0.0.1")
        for endpoint in [
            "http://neuronpedia.org",
            "https://evil.test",
            "http://192.168.1.2",
            "http://localhost:0",
            "http://user:pass@localhost",
            "https://www.neuronpedia.org?q=x",
            "http://localhost/%2e%2e/x",
        ]:
            with self.subTest(endpoint=endpoint), self.assertRaises(NeuronSteerError):
                np.endpoint_parts(endpoint)

    def test_invalid_flags_before_stdin_or_network(self):
        flags = [
            ["--timeout", "nan"],
            ["--timeout", "301"],
            ["--offset", "-1"],
            ["--limit", "101"],
            ["--strength", "inf"],
            ["--max-tokens", "0"],
            ["--seed", "100000001"],
            ["--api-key-env", "token-value!"],
        ]
        for values in flags:
            with self.subTest(values=values), self.assertRaises(NeuronSteerError):
                np.build_request(self.args("models", *values))
        with self.assertRaises(NeuronSteerError):
            np.build_request(self.args("activate", "--url", URL))
        with self.assertRaises(NeuronSteerError):
            np.build_request(self.args("models", "--stdin"))
        with self.assertRaises(NeuronSteerError):
            np.build_request(self.args("feature", "--url", URL, "--index", "0"))

    def test_dry_run_and_link_are_offline(self):
        with (
            mock.patch.object(np.NeuronpediaClient, "request") as request,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(main(["neuronpedia", "link", "--url", URL]), 0)
        self.assertFalse(json.loads(output.getvalue())["network_request_performed"])
        request.assert_not_called()
        with (
            mock.patch.object(np.NeuronpediaClient, "request") as request,
            mock.patch("sys.stdin", io.StringIO("synthetic")),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(
                main(["neuronpedia", "activate", "--url", URL, "--stdin", "--dry-run"]),
                0,
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["body"]["customText"], "synthetic")
        self.assertFalse(payload["text_submitted"])
        request.assert_not_called()

    def test_local_reference_is_annotation_only(self):
        with (
            mock.patch.object(np.NeuronpediaClient, "request") as request,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(
                main(
                    [
                        "generate",
                        "--model",
                        "/not-loaded",
                        "--neuron",
                        "0:0:0",
                        "--prompt",
                        "synthetic",
                        "--reference",
                        URL,
                        "--dry-run",
                    ]
                ),
                0,
            )
        self.assertEqual(json.loads(output.getvalue())["job"]["references"], [URL])
        request.assert_not_called()

    def response(self, status=200, data=b'{"data":[]}'):
        connection = mock.MagicMock()
        response = connection.getresponse.return_value
        response.status = status
        response.isclosed.return_value = False
        response.getheader.side_effect = lambda name, default=None: default
        response.read1.side_effect = [data, b""]
        return connection, response

    def test_tls_auth_and_no_proxies(self):
        connection, _ = self.response()
        tls = mock.MagicMock()
        with (
            mock.patch.object(
                np.http.client, "HTTPConnection", return_value=connection
            ) as factory,
            mock.patch.object(np.ssl, "create_default_context", return_value=tls),
            mock.patch.dict(
                os.environ, {"NP_TEST_KEY": "test-secret", "HTTPS_PROXY": "http://evil"}
            ),
        ):
            result = np.NeuronpediaClient(api_key_env="NP_TEST_KEY").request(
                "/api/model/list", {}, deadline=time.monotonic() + 5
            )
        self.assertEqual(result, {"data": []})
        self.assertEqual(factory.call_args.args, ("www.neuronpedia.org", 443))
        self.assertEqual(
            tls.wrap_socket.call_args.kwargs["server_hostname"], "www.neuronpedia.org"
        )
        self.assertEqual(
            connection.request.call_args.kwargs["headers"]["x-api-key"], "test-secret"
        )
        self.assertNotIn("test-secret", connection.request.call_args.args[1])
        connection.close.assert_called_once()

    def test_http_errors_no_retry_or_secret_echo(self):
        for status in (302, 401, 404, 429, 500):
            connection, _ = self.response(status)
            with (
                self.subTest(status=status),
                mock.patch.object(
                    np.http.client, "HTTPConnection", return_value=connection
                ),
                self.assertRaises(NeuronSteerError),
            ):
                np.NeuronpediaClient("http://localhost:3000").request(
                    "/api/model/list", {}, deadline=time.monotonic() + 5
                )
            self.assertEqual(connection.request.call_count, 1)
            connection.close.assert_called_once()

    def test_oversize_compressed_and_invalid_json_rejected(self):
        for headers, data in [
            ({"Content-Length": str(np.MAX_RESPONSE + 1)}, b"{}"),
            ({"Content-Encoding": "gzip"}, b"{}"),
            ({}, b'{"x":1,"x":2}'),
            ({}, b'{"x":NaN}'),
            ({}, b"not-json"),
        ]:
            connection, response = self.response(data=data)
            response.getheader.side_effect = lambda name, default=None: headers.get(
                name, default
            )
            with (
                self.subTest(headers=headers, data=data),
                mock.patch.object(
                    np.http.client, "HTTPConnection", return_value=connection
                ),
                self.assertRaises(NeuronSteerError),
            ):
                np.NeuronpediaClient("http://localhost:3000").request(
                    "/api/model/list", {}, deadline=time.monotonic() + 5
                )

    def test_mismatched_feature_resource_rejected(self):
        args = self.args("feature", "--url", URL)
        with (
            mock.patch.object(
                np.NeuronpediaClient, "request", return_value={"modelId": "wrong"}
            ),
            self.assertRaises(NeuronSteerError),
        ):
            np.run(args, time.monotonic() + 5, lambda deadline: None)

    def test_missing_or_malformed_key_rejected(self):
        with (
            mock.patch.dict(os.environ, {"NP_TEST_KEY": "x\r\nheader"}),
            self.assertRaises(NeuronSteerError),
        ):
            np.NeuronpediaClient(api_key_env="NP_TEST_KEY").request(
                "/api/model/list", {}, deadline=time.monotonic() + 5
            )
        with (
            mock.patch.dict(os.environ, {"NP_TEST_KEY": ""}),
            self.assertRaises(NeuronSteerError),
        ):
            np.NeuronpediaClient(api_key_env="NP_TEST_KEY").request(
                "/api/model/list", {}, deadline=time.monotonic() + 5
            )

    def test_incomplete_content_length_rejected(self):
        connection, response = self.response(data=b"{}")
        response.getheader.side_effect = lambda name, default=None: (
            "100" if name == "Content-Length" else default
        )
        with (
            mock.patch.object(
                np.http.client, "HTTPConnection", return_value=connection
            ),
            self.assertRaisesRegex(NeuronSteerError, "incomplete"),
        ):
            np.NeuronpediaClient("http://localhost:3000").request(
                "/api/model/list", {}, deadline=time.monotonic() + 5
            )

    def test_self_hosted_feature_url_uses_selected_endpoint(self):
        args = self.args("link", "--url", URL, "--endpoint", "http://localhost:3000")
        result = np.run(args, time.monotonic() + 5, lambda budget: None)
        self.assertTrue(result["feature_url"].startswith("http://localhost:3000/"))
        self.assertFalse(result["network_request_performed"])

    def test_local_result_and_checkpoint_preserve_references_without_network(self):
        import pathlib
        import tempfile

        import test_jobs

        from neuron_steer import jobs

        with tempfile.TemporaryDirectory() as directory:
            job = test_jobs.JobsTests().job(directory)
            job["references"] = [URL]
            pathlib.Path(job["prompts"]).write_text(
                '[{"category":"test","prompt":"synthetic"}]'
            )
            runtime = mock.Mock()
            runtime.info = {"device": "cpu"}
            runtime.generate.return_value = {
                "content": "synthetic",
                "tokens": 1,
                "elapsed_seconds": 0.1,
            }
            with (
                mock.patch.object(jobs, "LocalRuntime", return_value=runtime),
                mock.patch.object(np.NeuronpediaClient, "request") as request,
            ):
                jobs.run_job(job)
            payload = json.loads(pathlib.Path(job["output"]).read_text())
            self.assertEqual(payload["neuronpedia_references"], [URL])
            self.assertTrue(payload["references_are_annotations"])
            self.assertIn(URL, pathlib.Path(job["markdown_out"]).read_text())
            request.assert_not_called()

    def test_references_participate_in_identity(self):
        import pathlib
        import tempfile

        import test_jobs

        from neuron_steer import jobs

        with tempfile.TemporaryDirectory() as directory:
            job = test_jobs.JobsTests().job(directory)
            pathlib.Path(job["prompts"]).write_text(
                '[{"category":"test","prompt":"synthetic"}]'
            )
            prompts = jobs.load_prompts(pathlib.Path(job["prompts"]))
            before = jobs.checkpoint_identity(job, prompts)
            job["references"] = [URL]
            self.assertNotEqual(before, jobs.checkpoint_identity(job, prompts))
