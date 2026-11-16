import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import test_jobs

from neuron_steer import jobs
from neuron_steer.cli import main
from neuron_steer.steering import NeuronSteerError
from neuron_steer.storage import strict_json


class PolishTests(unittest.TestCase):
    def job(self, directory):
        job = test_jobs.JobsTests().job(directory)
        pathlib.Path(job["prompts"]).write_text(
            '[{"category":"test","prompt":"synthetic"}]'
        )
        return job

    def test_baseline_cli_dry_run_is_explicit_and_unhooked(self):
        with (
            contextlib.redirect_stdout(io.StringIO()) as out,
            mock.patch("neuron_steer.cli.bounded_run") as worker,
        ):
            self.assertEqual(
                main(
                    [
                        "generate",
                        "--model",
                        "/not-loaded",
                        "--baseline",
                        "--prompt",
                        "synthetic",
                        "--dry-run",
                    ]
                ),
                0,
            )
        data = json.loads(out.getvalue())
        self.assertEqual(data["targets"], [])
        self.assertTrue(data["job"]["baseline"])
        self.assertFalse(data["preflight"]["model_dimensions_validated"])
        worker.assert_not_called()

    def test_baseline_runtime_receives_no_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            job.update(
                operation="generate", prompt="synthetic", baseline=True, neurons=[]
            )
            runtime = mock.Mock()
            runtime.info = {}
            runtime.generate.return_value = {"content": "unhooked", "steering": None}
            with mock.patch.object(jobs, "LocalRuntime", return_value=runtime):
                result = jobs.run_job(job)
            self.assertEqual(result["mode"], "baseline")
            self.assertEqual(runtime.generate.call_args.args[1], [])
            job["neurons"] = ["0:1:1"]
            with self.assertRaises(NeuronSteerError):
                jobs.validate_job(job)

    def test_bad_local_options_fail_before_stdin(self):
        flags = [
            ["--neuron", "0:0:nan"],
            ["--neuron", "0:0:0", "--max-tokens", "0"],
            ["--baseline", "--temperature", "nan"],
        ]
        for targets in flags:
            with (
                self.subTest(targets=targets),
                mock.patch("neuron_steer.cli.read_prompt") as reader,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(
                    main(
                        [
                            "generate",
                            "--model",
                            "/not-loaded",
                            *targets,
                            "--stdin",
                            "--dry-run",
                        ]
                    ),
                    2,
                )
            reader.assert_not_called()

    def test_missing_resume_checkpoint_is_not_a_new_run(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            job["resume"] = True
            with (
                mock.patch.object(jobs, "LocalRuntime") as model,
                self.assertRaisesRegex(NeuronSteerError, "existing JSON checkpoint"),
            ):
                jobs.run_job(job)
            model.assert_not_called()
            self.assertFalse(pathlib.Path(job["output"]).exists())

    def test_directory_report_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            job["markdown_out"] = directory
            with self.assertRaisesRegex(NeuronSteerError, "must name files"):
                jobs.run_job(job)

    def test_assets_detect_tokenizer_changes_and_nested_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            prompts = jobs.load_prompts(pathlib.Path(job["prompts"]))
            model = pathlib.Path(job["model"])
            before = jobs.checkpoint_identity(job, prompts)
            (model / "merges.txt").write_text("token changes")
            second = jobs.checkpoint_identity(job, prompts)
            self.assertNotEqual(before, second)
            nested = model / "nested"
            nested.mkdir()
            (nested / "model-1.safetensors").write_bytes(b"local fixture")
            self.assertNotEqual(second, jobs.checkpoint_identity(job, prompts))

    def test_json_exponent_overflow_and_unpaired_surrogate_rejected(self):
        for source in [
            '{"x":1e999}',
            "[-1e999]",
            '"\\ud800"',
            '{"\\ud800":1}',
            '"\ud800"',
        ]:
            with self.subTest(source=repr(source)), self.assertRaises(NeuronSteerError):
                strict_json(source)
        self.assertEqual(strict_json('{"x":1e200}'), {"x": 1e200})

    def test_evaluation_dry_run_checks_prompt_limit_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            common = [
                "evaluate",
                "--model",
                job["model"],
                "--neuron",
                "0:0:0",
                "--prompts",
                job["prompts"],
                "--output",
                job["output"],
                "--dry-run",
            ]
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(common), 0)
            self.assertEqual(
                json.loads(out.getvalue())["preflight"]["prompts_selected"], 1
            )
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(common + ["--limit", "2"]), 2)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(common + ["--resume"]), 2)

    def test_optional_checkpoint_metadata_is_typed(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            runtime = mock.Mock()
            runtime.info = {}
            runtime.generate.return_value = {
                "content": "text",
                "tokens": 1,
                "elapsed_seconds": 0.1,
            }
            with mock.patch.object(jobs, "LocalRuntime", return_value=runtime):
                jobs.run_job(job)
            original = json.loads(pathlib.Path(job["output"]).read_text())
            job["resume"] = True
            for field, value in [
                ("input_tokens", True),
                ("input_tokens", -1),
                ("reached_token_budget", "false"),
            ]:
                payload = json.loads(json.dumps(original))
                payload["records"][0]["baseline"][field] = value
                pathlib.Path(job["output"]).write_text(json.dumps(payload))
                with (
                    self.subTest(field=field, value=value),
                    self.assertRaises(NeuronSteerError),
                ):
                    jobs.run_job(job)

    def test_checkpoint_format_version_must_be_integer(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            runtime = mock.Mock()
            runtime.info = {}
            runtime.generate.return_value = {
                "content": "text",
                "tokens": 1,
                "elapsed_seconds": 0.1,
            }
            with mock.patch.object(jobs, "LocalRuntime", return_value=runtime):
                jobs.run_job(job)
            path = pathlib.Path(job["output"])
            payload = json.loads(path.read_text())
            payload["version"] = 3.0
            path.write_text(json.dumps(payload))
            job["resume"] = True
            with self.assertRaisesRegex(NeuronSteerError, "unsupported checkpoint"):
                jobs.run_job(job)

    def test_symlink_loop_is_a_clean_cli_error(self):
        with tempfile.TemporaryDirectory() as directory:
            loop = pathlib.Path(directory) / "loop"
            loop.symlink_to(loop)
            with (
                contextlib.redirect_stderr(io.StringIO()) as err,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    main(
                        [
                            "generate",
                            "--model",
                            str(loop),
                            "--baseline",
                            "--prompt",
                            "synthetic",
                            "--dry-run",
                        ]
                    ),
                    2,
                )
            self.assertNotIn("Traceback", err.getvalue())

    def test_programmatic_job_expands_model_before_report_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.job(directory)
            model = pathlib.Path(job["model"])
            job["model"] = "~/fixture-model"
            job["markdown_out"] = str(model / "new-report.md")
            original = pathlib.Path.expanduser

            def expand(path):
                return model if str(path) == "~/fixture-model" else original(path)

            with (
                mock.patch.object(pathlib.Path, "expanduser", expand),
                self.assertRaisesRegex(NeuronSteerError, "outside the model"),
            ):
                jobs.run_job(job)
            self.assertFalse(pathlib.Path(job["markdown_out"]).exists())
