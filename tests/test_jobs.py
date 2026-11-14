import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from neuron_steer.cli import main
from neuron_steer import jobs
from neuron_steer.steering import NeuronSteerError
from neuron_steer.process import bounded_run


class JobsTests(unittest.TestCase):
    def job(self, directory):
        return dict(
            operation="evaluate",
            model=directory,
            neurons=["0:1:1"],
            max_tokens=4,
            temperature=0.1,
            context_size=20,
            seed=1,
            device="cpu",
            no_thinking=True,
            timeout=5,
            limit=0,
            resume=False,
            prompts=str(pathlib.Path(directory) / "prompts.json"),
            output=str(pathlib.Path(directory) / "out.json"),
            markdown_out=str(pathlib.Path(directory) / "out.md"),
        )

    def test_dry_run_independent_and_stdin(self):
        with (
            mock.patch.object(sys, "stdin", io.StringIO("synthetic")),
            contextlib.redirect_stdout(io.StringIO()) as out,
            mock.patch("neuron_steer.cli.bounded_run") as worker,
        ):
            status = main(
                [
                    "generate",
                    "--model",
                    "/missing",
                    "--neuron",
                    "0:0:0",
                    "--stdin",
                    "--dry-run",
                ]
            )
        self.assertEqual(status, 0)
        self.assertFalse(json.loads(out.getvalue())["model_loaded"])
        worker.assert_not_called()

    def test_bad_values_and_missing_model_worker(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "generate",
                        "--model",
                        "/missing",
                        "--neuron",
                        "0:0:nan",
                        "--prompt",
                        "text",
                        "--dry-run",
                    ]
                ),
                2,
            )
            self.assertEqual(
                main(
                    [
                        "generate",
                        "--model",
                        "/missing",
                        "--neuron",
                        "0:0:0",
                        "--prompt",
                        "text",
                    ]
                ),
                2,
            )

    def test_child_timeout_and_output_limits(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            bounded_run(
                [sys.executable, "-c", "import time;time.sleep(10)"], timeout=0.05
            )
        with self.assertRaises(NeuronSteerError):
            bounded_run(
                [sys.executable, "-c", 'print("x"*10000)'], max_bytes=100, timeout=5
            )
        result = bounded_run(
            [sys.executable, "-c", "import sys;print(sys.stdin.read())"],
            input="synthetic",
            timeout=5,
        )
        self.assertEqual(result.stdout.strip(), "synthetic")

    def test_paired_evaluation_and_resume_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            pathlib.Path(job["prompts"]).write_text(
                json.dumps(
                    [
                        {"category": "test", "prompt": "synthetic one"},
                        {"category": "test", "prompt": "synthetic two"},
                    ]
                )
            )
            runtime = mock.Mock()
            runtime.info = {"device": "cpu"}
            runtime.generate.return_value = {
                "content": "same",
                "tokens": 1,
                "elapsed_seconds": 0.01,
            }
            with mock.patch.object(jobs, "LocalRuntime", return_value=runtime):
                reply = jobs.run_job(job)
            self.assertEqual(reply["summary"]["prompts"], 2)
            self.assertEqual(runtime.generate.call_count, 4)
            self.assertEqual(runtime.generate.call_args_list[0].args[1], [])
            self.assertEqual(
                runtime.generate.call_args_list[0].kwargs,
                runtime.generate.call_args_list[1].kwargs,
            )
            job["resume"] = True
            with mock.patch.object(jobs, "LocalRuntime") as loader:
                self.assertEqual(jobs.run_job(job)["summary"]["prompts"], 2)
                loader.assert_not_called()
            job["max_tokens"] = 5
            with self.assertRaisesRegex(NeuronSteerError, "resume settings"):
                jobs.run_job(job)

    def test_output_cannot_overwrite_prompts(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            pathlib.Path(job["prompts"]).write_text(
                '[{"category":"test","prompt":"synthetic"}]'
            )
            job["output"] = job["prompts"]
            with self.assertRaises(NeuronSteerError):
                jobs.run_job(job)

    def test_checkpoint_survives_later_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            pathlib.Path(job["prompts"]).write_text(
                '[{"category":"test","prompt":"one"},{"category":"test","prompt":"two"}]'
            )
            runtime = mock.Mock()
            runtime.info = {}
            runtime.generate.side_effect = [
                dict(content="a", tokens=1, elapsed_seconds=0),
                dict(content="b", tokens=1, elapsed_seconds=0),
                RuntimeError("generation failed"),
            ]
            with (
                mock.patch.object(jobs, "LocalRuntime", return_value=runtime),
                self.assertRaises(RuntimeError),
            ):
                jobs.run_job(job)
            self.assertEqual(
                len(json.loads(pathlib.Path(job["output"]).read_text())["records"]), 1
            )

    def test_bundled_prompts(self):
        path = (
            pathlib.Path(jobs.__file__).parent / "prompts/neuron_steer_prompts_100.json"
        )
        rows = jobs.load_prompts(path)
        self.assertEqual(len(rows), 100)
        self.assertEqual(len({r["prompt"] for r in rows}), 100)
