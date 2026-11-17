import contextlib
import importlib.util
import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import test_transformers_integration as fixtures

from neuron_steer import adapters
from neuron_steer.cli import main
from neuron_steer.jobs import run_job
from neuron_steer.provenance import digest, hash_file
from neuron_steer.runtime import LocalRuntime
from neuron_steer.steering import NeuronSteerError
from neuron_steer.storage import read_json, write_json


class AdapterValidationTests(unittest.TestCase):
    def make_job(self, directory):
        root = pathlib.Path(directory)
        base = root / "base"
        base.mkdir()
        train = root / "train.json"
        val = root / "validation.json"
        train.write_text(json.dumps([{"prompt": "hello", "response": "answer"}]))
        val.write_text(json.dumps([{"prompt": "world", "response": "answer"}]))
        return {
            "operation": "adapter",
            "action": "train",
            "model": str(base),
            "train_data": str(train),
            "validation_data": str(val),
            "output_dir": str(root / "adapter"),
            "target_modules": ["c_proj"],
        }

    def managed_stub(self, directory):
        path = pathlib.Path(directory) / "adapter"
        path.mkdir()
        (path / "adapter_model.safetensors").write_bytes(
            b"synthetic, not loaded by validation tests"
        )
        (path / "adapter_config.json").write_text(
            json.dumps({"peft_type": "LORA", "task_type": "CAUSAL_LM"})
        )
        assets = [{"path": "model.safetensors", "bytes": 1, "sha256": "0" * 64}]
        data = {
            "artifact_version": 1,
            "kind": "lora",
            "base_identity": {"assets": assets, "sha256": digest(assets)},
            "assets": adapters.artifact_assets(path),
            "policy": {
                "system_role": "merge",
                "chat_template": None,
                "no_thinking": False,
                "max_seq_length": 128,
            },
            "train_examples": [{"prompt": "hello", "response": "answer"}],
            "validation_examples": [{"prompt": "world", "response": "answer"}],
        }
        write_json(path / adapters.ADAPTER_MANIFEST, data)
        return path, data

    def test_dry_run_validates_without_optional_runtime_or_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(directory)
            with (
                mock.patch.object(adapters, "require_peft") as peft,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                result = main(
                    [
                        "adapter",
                        "train",
                        "--model",
                        job["model"],
                        "--train-data",
                        job["train_data"],
                        "--validation-data",
                        job["validation_data"],
                        "--target-module",
                        "c_proj",
                        "--output-dir",
                        job["output_dir"],
                        "--dry-run",
                    ]
                )
            self.assertEqual(result, 0)
            peft.assert_not_called()
            self.assertFalse(pathlib.Path(job["output_dir"]).exists())
            self.assertFalse(json.loads(output.getvalue())["model_loaded"])

    def test_invalid_hyperparameters_and_targets_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(directory)
            for change in (
                {"rank": True},
                {"rank": 129},
                {"steps": 0},
                {"steps": 10**1000},
                {"learning_rate": float("nan")},
                {"alpha": 0},
                {"target_modules": [".*"]},
                {"target_modules": ["c_proj", "c_proj"]},
                {"max_seq_length": 4096},
                {"max_validation_regression": -1},
                {"gradient_checkpointing": "yes"},
            ):
                with self.subTest(change=change), self.assertRaises(NeuronSteerError):
                    adapters.validate_adapter_job({**job, **change})

    def test_invalid_rows_and_normalized_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(directory)
            p = pathlib.Path(job["train_data"])
            for rows in (
                [],
                [{"prompt": "hello", "response": ""}],
                [{"prompt": "hello", "answer": "answer"}],
                [
                    {"prompt": "hello", "response": "answer"},
                    {"prompt": " HELLO ", "response": "answer"},
                ],
            ):
                p.write_text(json.dumps(rows))
                with self.assertRaises(NeuronSteerError):
                    adapters.validate_adapter_job(dict(job))
            p.write_text(json.dumps([{"prompt": " WORLD ", "response": "answer"}]))
            with self.assertRaisesRegex(NeuronSteerError, "disjoint"):
                adapters.validate_adapter_job(job)

    def test_fresh_paths_and_model_symlink_alias_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(directory)
            root = pathlib.Path(directory)
            for target in (
                job["model"],
                str(pathlib.Path(job["model"]) / "child"),
                job["train_data"],
            ):
                with self.subTest(target=target), self.assertRaises(NeuronSteerError):
                    adapters.validate_adapter_job({**job, "output_dir": target})
            case_alias = pathlib.Path(job["model"]).with_name("BASE")
            if case_alias.is_dir():
                with self.assertRaises(NeuronSteerError):
                    adapters.validate_adapter_job(
                        {**job, "output_dir": str(case_alias / "child")}
                    )
            alias = root / "alias"
            alias.symlink_to(job["model"], target_is_directory=True)
            with self.assertRaises(NeuronSteerError):
                adapters.validate_adapter_job(
                    {**job, "output_dir": str(alias / "child")}
                )

    def test_failed_publication_removes_partial_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(directory)
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                with adapters.publish(job) as stage:
                    (stage / "partial.txt").write_text("partial")
                    raise RuntimeError("synthetic")
            self.assertFalse(pathlib.Path(job["output_dir"]).exists())
            self.assertFalse(list(pathlib.Path(directory).glob(".adapter.partial-*")))

    def test_policy_inherited_and_heldout_data_required(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.make_job(directory)
            path, data = self.managed_stub(directory)
            generation = {
                "operation": "adapter",
                "action": "generate",
                "model": job["model"],
                "adapter": str(path),
                "prompt": "new question",
            }
            adapters.validate_adapter_job(generation)
            self.assertEqual(generation["system_role"], "merge")
            self.assertFalse(generation["no_thinking"])
            prompts = pathlib.Path(directory) / "prompts.json"
            prompts.write_text(
                json.dumps([{"category": "synthetic", "prompt": " HELLO "}])
            )
            with self.assertRaisesRegex(NeuronSteerError, "held-out"):
                adapters.validate_adapter_job(
                    {
                        "operation": "adapter",
                        "action": "evaluate",
                        "model": job["model"],
                        "adapter": str(path),
                        "prompts": str(prompts),
                        "output_dir": str(pathlib.Path(directory) / "evaluation"),
                    }
                )

    def test_corrupt_manifest_escaping_assets_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, data = self.managed_stub(directory)
            for mutation in (
                {"artifact_version": True},
                {"policy": {}},
                {"validation_examples": []},
                {"assets": [{"path": "../escape"}]},
            ):
                write_json(path / adapters.ADAPTER_MANIFEST, {**data, **mutation})
                with (
                    self.subTest(mutation=mutation),
                    self.assertRaises(NeuronSteerError),
                ):
                    adapters.read_adapter(path)
            write_json(path / adapters.ADAPTER_MANIFEST, data)
            weight = path / "adapter_model.safetensors"
            weight.unlink()
            weight.symlink_to(path / "adapter_config.json")
            with self.assertRaisesRegex(NeuronSteerError, "symlink"):
                adapters.read_adapter(path)

    def test_unlisted_symlinks_rejected_before_hashing_outside_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path, data = self.managed_stub(directory)
            outside = pathlib.Path(directory) / "outside.txt"
            outside.write_text("synthetic outside file")
            (path / "extra.txt").symlink_to(outside)
            with (
                mock.patch.object(adapters, "hash_file") as hasher,
                self.assertRaisesRegex(NeuronSteerError, "symlink"),
            ):
                adapters.verify_assets(path, data)
            hasher.assert_not_called()

    def test_changed_assets_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, data = self.managed_stub(directory)
            (path / "adapter_model.safetensors").write_bytes(b"changed")
            with self.assertRaisesRegex(NeuronSteerError, "changed"):
                adapters.verify_assets(path, data)


HAS_TRAINING = (
    fixtures.torch is not None
    and sys.version_info >= (3, 11)
    and importlib.util.find_spec("peft") is not None
)


@unittest.skipUnless(HAS_TRAINING, "optional PEFT training runtime unavailable")
class RealAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls.temp.name)
        cls.model = cls.root / "base"
        fixtures.TransformersIntegrationTests().runtime(str(cls.model))
        cls.original = {
            p.name: hash_file(p) for p in cls.model.iterdir() if p.is_file()
        }
        cls.train = cls.root / "train.json"
        cls.validation = cls.root / "validation.json"
        cls.train.write_text(json.dumps([{"prompt": "hello", "response": "answer"}]))
        cls.validation.write_text(
            json.dumps([{"prompt": "world", "response": "answer"}])
        )
        cls.job = {
            "operation": "adapter",
            "action": "train",
            "model": str(cls.model),
            "device": "cpu",
            "train_data": str(cls.train),
            "validation_data": str(cls.validation),
            "output_dir": str(cls.root / "adapter"),
            "target_modules": ["c_proj"],
            "rank": 4,
            "alpha": 8,
            "steps": 60,
            "learning_rate": 0.01,
            "max_seq_length": 128,
            "seed": 0,
        }
        cls.result = run_job(dict(cls.job))
        cls.data = read_json(cls.root / "adapter" / adapters.ADAPTER_MANIFEST)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def operation(self, action, **options):
        return {
            "operation": "adapter",
            "action": action,
            "model": str(self.model),
            "adapter": str(self.root / "adapter"),
            "device": "cpu",
            **options,
        }

    def test_training_changes_adapter_and_improves_toy_validation_loss(self):
        self.assertLess(
            self.result["validation_loss"]["adapter"],
            self.result["validation_loss"]["base"],
        )
        self.assertEqual(
            {p.name: hash_file(p) for p in self.model.iterdir() if p.is_file()},
            self.original,
        )
        self.assertLess(self.data["reload_verification"]["max_loss_error"], 0.0001)
        self.assertGreater(self.result["trainable_parameters"], 0)
        self.assertFalse(self.result["behavior_improvement_verified"])
        from safetensors.torch import load_file

        weights = load_file(str(self.root / "adapter" / "adapter_model.safetensors"))
        self.assertTrue(
            any(
                bool((w != 0).any().item())
                for name, w in weights.items()
                if "lora_B" in name
            )
        )

    def test_response_only_labels_and_eos(self):
        row = self.data["encoded_train"][0]
        labels = row["labels"][0]
        prompt_count = labels.index(next(v for v in labels if v != -100))
        self.assertTrue(all(v == -100 for v in labels[:prompt_count]))
        self.assertEqual(labels[-2:], [7, 2])
        self.assertEqual(row["supervised_tokens"], 2)
        self.assertEqual(row["model_inputs"]["input_ids"]["values"][0].count(1), 1)

    def test_fresh_load_generates_without_steering_hooks(self):
        result = run_job(
            self.operation("generate", prompt="hello answer", max_tokens=3)
        )
        self.assertEqual(result["mode"], "trained-adapter")
        self.assertIsNone(result["steering"])
        self.assertIn("adapter_identity", result["runtime"])

    def test_evaluation_uses_disjoint_questions_and_saves_pair(self):
        prompts = self.root / "test.json"
        prompts.write_text(
            json.dumps(
                [
                    {
                        "category": "synthetic",
                        "prompt": "hello answer",
                        "answers": ["answer"],
                    }
                ]
            )
        )
        output = self.root / "evaluation"
        result = run_job(
            self.operation(
                "evaluate", prompts=str(prompts), output_dir=str(output), max_tokens=3
            )
        )
        payload = read_json(output / "evaluation.json")
        self.assertEqual(result["summary"]["generations"], 2)
        self.assertEqual(payload["records"][0]["order"], ["baseline", "adapter"])
        self.assertIsNone(payload["records"][0]["baseline"]["steering"])
        self.assertFalse(payload["summary"]["truthfulness_verified"])

    def test_disabled_adapter_matches_unadapted_base_loss(self):
        job = self.operation("generate", prompt="hello")
        adapters.validate_adapter_job(job)
        experiment = adapters.experiment_identity(job)
        runtime = LocalRuntime(self.model, "cpu")
        example = adapters.encode_example(
            runtime, self.data["validation_examples"][0], self.data["policy"], 128
        )
        raw = adapters.supervised_loss(runtime, [example])
        adapters.load_managed(runtime, job, experiment)
        with runtime.model.disable_adapter():
            disabled = adapters.supervised_loss(runtime, [example])
        enabled = adapters.supervised_loss(runtime, [example])
        self.assertAlmostEqual(raw, disabled, places=6)
        self.assertLess(enabled, disabled)

    def test_gradient_checkpointing_training_can_reload(self):
        target = self.root / "checkpointed-adapter"
        job = {
            **self.job,
            "output_dir": str(target),
            "steps": 1,
            "gradient_checkpointing": True,
            "target_modules": ["transformer.h.0.mlp.c_proj"],
            "max_validation_regression": 1.0,
        }
        run_job(job)
        data = read_json(target / adapters.ADAPTER_MANIFEST)
        self.assertLess(data["reload_verification"]["max_loss_error"], 0.0001)

    def test_merge_reloads_standalone_and_matches_adapter_probabilities(self):
        output = self.root / "merged"
        result = run_job(
            self.operation("merge", output_dir=str(output), tolerance=0.0001)
        )
        self.assertLess(
            result["verification"]["max_answer_log_probability_error"], 0.0001
        )
        reloaded = LocalRuntime(output, "cpu")
        self.assertFalse(getattr(reloaded.model, "peft_config", None))
        self.assertTrue(all(not m._forward_hooks for m in reloaded.model.modules()))
        self.assertEqual(
            {p.name: hash_file(p) for p in self.model.iterdir() if p.is_file()},
            self.original,
        )
        self.assertFalse((output / "adapter_config.json").exists())

    def test_wrong_base_or_changed_adapter_rejected_before_loading(self):
        copy = self.root / "wrong-base"
        shutil.copytree(self.model, copy)
        copied = run_job(
            {
                **self.operation("generate", prompt="hello", max_tokens=3),
                "model": str(copy),
            }
        )
        self.assertEqual(copied["mode"], "trained-adapter")
        (copy / "config.json").write_text((copy / "config.json").read_text() + "\n")
        with (
            mock.patch.object(adapters, "LocalRuntime") as loader,
            self.assertRaisesRegex(NeuronSteerError, "different base"),
        ):
            run_job({**self.operation("generate", prompt="hello"), "model": str(copy)})
        loader.assert_not_called()
        path = self.root / "changed-adapter"
        shutil.copytree(self.root / "adapter", path)
        (path / "adapter_model.safetensors").write_bytes(b"corrupted")
        with (
            mock.patch.object(adapters, "LocalRuntime") as loader,
            self.assertRaisesRegex(NeuronSteerError, "changed"),
        ):
            run_job(
                {**self.operation("generate", prompt="hello"), "adapter": str(path)}
            )
        loader.assert_not_called()

    def test_saved_training_tokenization_mismatch_is_rejected(self):
        path = self.root / "changed-encoding"
        shutil.copytree(self.root / "adapter", path)
        data = read_json(path / adapters.ADAPTER_MANIFEST)
        data["encoded_validation"][0]["model_inputs"]["input_ids"]["values"][0][0] = 0
        write_json(path / adapters.ADAPTER_MANIFEST, data)
        with self.assertRaisesRegex(
            NeuronSteerError, "saved training tokenization differs"
        ):
            run_job(
                {**self.operation("generate", prompt="hello"), "adapter": str(path)}
            )

    def test_unknown_and_tied_targets_rejected(self):
        runtime = LocalRuntime(self.model, "cpu")
        with self.assertRaisesRegex(NeuronSteerError, "does not exist"):
            adapters.select_modules(runtime, ["unknown_projection"])
        with self.assertRaisesRegex(NeuronSteerError, "shared/tied"):
            adapters.select_modules(runtime, ["lm_head"])

    def test_linear_llama_adapter_merge_parity(self):
        import transformers

        torch = fixtures.torch
        model = self.root / "llama-base"
        torch.manual_seed(0)
        base = transformers.LlamaForCausalLM(
            transformers.LlamaConfig(
                vocab_size=8,
                hidden_size=16,
                intermediate_size=32,
                num_hidden_layers=1,
                num_attention_heads=2,
                num_key_value_heads=2,
                max_position_embeddings=256,
                bos_token_id=1,
                eos_token_id=2,
                pad_token_id=2,
            )
        )
        base.save_pretrained(model, safe_serialization=True)
        tokenizer = fixtures.TransformersIntegrationTests().tokenizer()
        tokenizer.model_input_names = ["input_ids", "attention_mask"]
        tokenizer.save_pretrained(model)
        output = self.root / "llama-adapter"
        result = run_job(
            {
                **self.job,
                "model": str(model),
                "output_dir": str(output),
                "target_modules": ["down_proj"],
                "steps": 20,
                "max_validation_regression": 1.0,
            }
        )
        self.assertGreater(result["trainable_parameters"], 0)
        merged = self.root / "llama-merged"
        result = run_job(
            {
                **self.operation("merge", output_dir=str(merged), tolerance=0.0001),
                "model": str(model),
                "adapter": str(output),
            }
        )
        self.assertLess(
            result["verification"]["max_answer_log_probability_error"], 0.0001
        )
        self.assertFalse(
            getattr(LocalRuntime(merged, "cpu").model, "peft_config", None)
        )

    def test_training_context_rejects_instead_of_truncating(self):
        runtime = LocalRuntime(self.model, "cpu")
        runtime.model.config.return_dict = False
        example = adapters.encode_example(
            runtime, {"prompt": "hello", "response": "answer"}, self.data["policy"], 128
        )
        self.assertGreater(adapters.supervised_loss(runtime, [example]), 0)
        with self.assertRaisesRegex(NeuronSteerError, "bound"):
            adapters.encode_example(
                runtime,
                {"prompt": "hello", "response": "answer"},
                self.data["policy"],
                8,
            )

    def test_validation_gate_publishes_nothing_on_regression(self):
        job = {**self.job, "output_dir": str(self.root / "regressed"), "steps": 1}
        with (
            mock.patch.object(adapters, "supervised_loss", side_effect=[1.0, 2.0]),
            self.assertRaisesRegex(NeuronSteerError, "regression"),
        ):
            run_job(job)
        self.assertFalse(pathlib.Path(job["output_dir"]).exists())

    def test_oversize_manifest_prevents_adapter_publication(self):
        from neuron_steer import storage

        target = self.root / "oversize-adapter"

        def bounded_write(path, payload, **kwargs):
            return storage.write_json(path, payload, max_bytes=1)

        with (
            mock.patch.object(adapters, "write_json", side_effect=bounded_write),
            self.assertRaisesRegex(NeuronSteerError, "byte limit"),
        ):
            run_job(
                {
                    **self.job,
                    "output_dir": str(target),
                    "steps": 1,
                    "max_validation_regression": 1.0,
                }
            )
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob(".oversize-adapter.partial-*")))

    def test_failed_export_parity_removes_partial_model(self):
        target = self.root / "bad-merge"
        with (
            mock.patch.object(
                adapters, "answer_log_probs", side_effect=[[[0.0]], [[1.0]]]
            ),
            self.assertRaisesRegex(NeuronSteerError, "mismatch"),
        ):
            run_job(self.operation("merge", output_dir=str(target), tolerance=0.0001))
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob(".bad-merge.partial-*")))
