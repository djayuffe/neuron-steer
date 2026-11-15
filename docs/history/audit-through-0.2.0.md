# Historical audit notes through 0.2.0

Preserved for provenance; current behavior is documented in the root guides.

# Integration audit — 0.2.0 (2026-11-15)

Added explicit remote research without changing the offline generation path.
Verified URL/host restrictions, credential headers, stdin opt-in, hard process
timeout, payload contracts, response bounds/strict JSON and exact feature identity.
Fixed two integration edge cases during testing: truncated bodies declaring a
larger Content-Length are rejected, and self-hosted dashboard links use the
selected endpoint rather than implying a public resource exists.

Research references are canonical URLs only; they cannot invoke a client, install
hooks, map SAE coordinates into MLP indices or load weights. They participate in
resume identity. Detailed design and limits are in [design.md](../../design.md), and
verification is recorded in `verification/neuronpedia-v0.2.0.json`.

---

# Audit — 0.1.1 (2026-11-15)

Scope: standalone `neuron-steer`, its worker, runtime hooks, report/checkpoint
handling, package build and CI. Historical CallAI reports were retained as source
artifacts; they were not rerun or silently rewritten. This is a bounded audit,
not a claim that every architecture, dependency version or failure is covered.

| ID | Finding | Change and evidence |
| --- | --- | --- |
| A01 | Evaluation could replace model files via `--output` or `--markdown-out`. | Reject report paths within the model directory, symlinks, hard links to model files, and aliases of prompt files. Regression tests check files remain intact. |
| A02 | A new evaluation could overwrite an unrelated Markdown document. | Existing JSON or Markdown is rejected unless resuming the matching checkpoint. Markdown path is part of identity. |
| A03 | Fixed `.tmp` report names could overwrite unrelated files or collide. | Unique same-directory temporary files, fsync before atomic replacement, and cleanup on failure. The old `.tmp` name remains untouched in tests. |
| A04 | Concurrent evaluations could corrupt a shared checkpoint/report pair. | Non-blocking OS locks for both report paths, acquired in stable order. A competing writer fails before loading a model. Lock files persist; a process exit releases their OS locks. |
| A05 | Partial or edited checkpoint records could crash summary generation or carry invalid counts. | Strict format/version, record indices/categories/text, token/time fields and metrics checks before loading weights. Reject booleans as indices and recompute expected metrics for comparison. |
| A06 | Resume did not identify package/runtime changes. | v2 fingerprint adds package, Python and installed runtime versions, Markdown path and prior input/model-file metadata. A partial resume additionally compares resolved runtime metadata. v1 checkpoints require a fresh report. |
| A07 | JSON accepted duplicate keys and non-finite numbers; file reads lacked limits. | Shared strict JSON decoder with byte/depth bounds; prompt files limited to 8 MiB, 10000 prompts and 200-character categories; checkpoints limited to 128 MiB. Writers reject non-finite JSON. |
| A08 | Model text could terminate a report code fence; prompt text could alter Markdown structure. | Dynamic fences longer than any contained tilde run and escaped literal labels/prompts. Existing historical reports remain unsanitized originals. |
| A09 | Reports always said “100 prompts” and counted both arms in each Runs column. | Titles show the actual prompt count; each Runs column shows its own arm's count. |
| A10 | Prompt loading stripped meaningful leading/trailing whitespace. | Preserve exact prompt text after validating its bounds. |
| A11 | Projection vectors could overflow a half-precision dtype despite finite calibration. | Compute scaling in float32, cast to projection dtype, reject non-finite vectors before installing hooks. Real torch regression reproduces float16 overflow and checks cleanup. Reject quantized/integer weight representations recognized by tensor flags. |
| A12 | Calibration could advance RNG state before a paired sample. | Reset the seed immediately before generation, after calibration. Sampling settings remain identical between arms. |
| A13 | Model configuration could return a generation object where a tensor was expected. | Explicitly request `return_dict_in_generate=False`; continue reporting EOS-aware token-budget exhaustion. |
| A14 | Malformed worker error arrays could cause an uncaught attribute error. | Strict response JSON and object checks produce a bounded user-facing diagnostic. Numeric integer fields reject floats and booleans. |
| A15 | CI and local lint defaults differed. | Retrieved CI diagnostics: Ruff import/style rules failed after tests passed. Pin Ruff 0.17.5, declare project rules explicitly, fix import/style findings and disable matrix fail-fast so every platform reports its result. |

## Remaining limits

- Supported platforms are macOS and Linux. Worker process groups, pipes and report
  locks use POSIX APIs. Windows is not supported.
- Model-file identity uses file names, sizes and modification times rather than
  hashing gigabytes of weights; deliberate same-metadata modifications are not
  detected. Fingerprints and checkpoints are not cryptographic attestations of
  generated content or model provenance.
- File validation and locks prevent accidental collisions among cooperative
  evaluations. They are not a security boundary against a local process changing
  files/directories during an operation. Locks require normal POSIX filesystem
  semantics; unusual network filesystems may differ.
- JSON and Markdown are each atomically replaced, not committed as a single
  transaction. A crash can leave a current JSON checkpoint with stale/missing
  Markdown; `--resume` regenerates Markdown from a complete matching checkpoint.
- Offline library settings disable supported model downloads; they do not create
  an OS network sandbox. Optional libraries and installed environments remain
  trusted local software. No new model weights were downloaded for this audit.
- Dense supported MLP layouts only. An unrecognized packed/quantized/custom
  projection is not guaranteed to work. Target behavior is model dependent.
- Token equality is not promised across hardware or library versions, even with
  a fixed seed. Keyword/text metrics describe outputs and do not establish
  quality, safety, entailment or causality.

## Validation

See [verification/standalone-audit-v0.1.1.json](../../verification/standalone-audit-v0.1.1.json)
for exact checks, test counts and live-smoke scope. Tests cover the regressions
above, context bounds, temporary hook removal, baseline controls, worker timeout
and output limits. Historical experiment files are documented separately in
[verification/README.md](../../verification/README.md).
