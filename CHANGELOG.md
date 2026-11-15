# Changelog

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
