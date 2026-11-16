# Changelog

## 0.3.0 — 2026-11-16

Fix duplicate chat BOS tokens, real Jinja role rejection, named-template thinking
flags and false hook-application claims. Add full prompt/config/source manifests,
optional model asset SHA256, explicit calibration policy and conservative token
budget accounting. Force one returned sequence and reject incompatible minimum
completion budgets. Add seeds, dose sweeps, alternating order, zero/random/reversed/
leave-one-out controls, answer-key scoring and prompt-cluster bootstrap intervals.
Add real offline Transformers regression jobs alongside the core platform matrix.
Handle macOS process-group permission races with direct-child termination, a
bounded cleanup wait and unconditional pipe closure.

Checkpoint format is **v3**. v1/v2 cannot resume; start fresh report paths.
Existing historical reports remain readable. No weights are downloaded or edited.

## 0.2.1 — 2026-11-15

Add explicit unhooked `generate --baseline` and distinguish it from zero-dose
hooks. Validate local options before stdin; evaluation dry-runs preflight prompt
sets, limits and output paths. Reject missing resume checkpoints and non-file
report targets; normalize programmatic job paths and handle symlink loops cleanly. Track nested safetensors and tokenizer/vocabulary assets in model
identity. Reject JSON exponent overflow and invalid Unicode; validate optional
checkpoint counts/flags and integer format versions. Consolidate current docs,
retain historical audits under `docs/history`, and refresh examples/verification.
Package-version identity changes require fresh report paths after upgrading.

## 0.2.0 — 2026-11-15

Add an independent Neuronpedia API client and CLI: discovery, dashboard lookup,
explanation search, activation/top-feature tests, hosted additive steering and
offline feature links. Restrict hosted traffic to official verified HTTPS origins
or explicit loopback webapps, keep credentials in headers, enforce the command
budget through a bounded worker, and validate response limits/identity.

Local jobs may carry up to 16 research references; no reference is fetched or
converted to a neuron coordinate. References are included in checkpoint identity
and reports. Add detailed `design.md`, usage guide and contract/loopback tests.
Reject incomplete API bodies and use the selected self-hosted origin in feature
links. Package upgrades require fresh report paths; existing reports are retained.

## 0.1.1 — 2026-11-15

Protect model/input files from report writes; prevent competing report writers;
use unique atomic temporary files; validate bounded strict JSON and checkpoint
records. Escape Markdown structure, preserve prompt whitespace and correct report
counts. Reject half-precision vector overflow, reset generation RNG after
calibration and enforce tensor generation results. Make linting reproducible in
CI and document supported platforms, limits and verification provenance.

Checkpoint format is now **v2**. Existing v1 files remain readable as historical
JSON reports but cannot be resumed by 0.1.1. Choose new report paths for a new run;
no weight downloads or edits are part of this upgrade. See [AUDIT.md](AUDIT.md).

## 0.1.0 — 2026-11-14

Independent package extraction with CLI, bounded subprocess worker, local
Transformers runtime, temporary neuron hooks, paired evaluation and wheel build.
