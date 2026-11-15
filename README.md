# neuron-steer

Version **0.2.0**. Supported platforms: **macOS and Linux**, Python 3.10+.
Read the detailed [design.md](design.md) for the math and execution flow, and
[NEURONPEDIA.md](NEURONPEDIA.md) for the explicit research API.
See [AUDIT.md](AUDIT.md) for findings, fixes and limits, and
[CHANGELOG.md](CHANGELOG.md) for the v2 checkpoint upgrade.
[ARCHITECTURE.md](ARCHITECTURE.md) describes module boundaries and maintenance.

Standalone local neuron steering and paired text-generation evaluation for
compatible Hugging Face Transformers causal language models. This project is
independent of CallAI: it does not import its code, require its checkout, or read
call/message history.

For local generation, the package loads already-downloaded local safetensors weights with offline
Hugging Face settings and remote model code disabled. It applies temporary MLP
output-projection hooks and removes them after generation, including error paths.
It does not edit weight files or provide tools to the generated model.

## Neuronpedia research

```sh
python -m neuron_steer neuronpedia models
python -m neuron_steer neuronpedia feature --url https://www.neuronpedia.org/gemma-2-2b/20-gemmascope-res-16k/10956
```

Discover models/sources, look up feature explanations, search explanations, test
activations, find top features and request service-side steering. Text is supplied
explicitly via stdin; hosted text may be retained. `link` and `--dry-run` are
offline. An optional local `--reference URL` stores research annotations without
fetching them or converting SAE feature indices into local MLP targets. See
[NEURONPEDIA.md](NEURONPEDIA.md) for self-hosting, credentials and examples.

## Run without installing

From this directory, use a Python **3.10+** environment. The examples call it
`python`; choose an explicit supported interpreter if your shell selects an older
Python. Live generation additionally needs the model runtime packages:

```sh
python -m neuron_steer generate --model /path/to/local/model \
  --neuron 0:0:0 --prompt 'Write a short greeting.' --dry-run
```

Dry runs need neither model weights nor torch/Transformers. For live generation,
use an environment where compatible `torch`, `transformers`, and `safetensors`
packages are installed:

```sh
printf '%s' 'Write a short greeting.' | python -m neuron_steer generate \
  --model /path/to/local/model --neuron 0:0:0 --stdin \
  --device mps --max-tokens 32 --timeout 120
```

The zero-dose target is a control; choose explicit model-specific targets for an
actual intervention. Repeat `--neuron LAYER:NEURON:DOSE` for up to eight targets.
Layer and neuron numbering is zero based. No targets are inferred from a model
name. Dimensions are checked against the loaded projection. Positive and negative
doses add or subtract a calibrated vector. Indices are not portable across models,
architectures, fine-tunes, or quantizations.

## Install as a package

```sh
python -m pip install -e .              # CLI and dry runs only
python -m pip install -e '.[runtime]'   # also installs optional runtime packages
neuron-steer --help
```

Installing runtime dependencies requires package downloads if absent; model
weights are never downloaded by this application. Package minimum versions are
not a guarantee that every supported architecture exists in an older runtime;
use the runtime required by your particular local model. CPU, MPS and CUDA depend
on available hardware and installed libraries. A local Transformers checkpoint is
required; Ollama/GGUF/MLX weights are not directly loaded by this implementation.

## Paired evaluation

The default prompt set contains 100 explicit prompts. A model is loaded once per
evaluation. Each prompt runs once without hooks and once with the selected hooks;
both sides reset the same seed and use the same generation options.

```sh
python -m neuron_steer evaluate --model /path/to/local/model \
  --neuron 0:0:0 --limit 2 --max-tokens 32 \
  --output results/control.json --timeout 300
```

Use `--prompts prompts/neuron_steer_prompts_20.json` for the screening set, or a
JSON list containing `{ "category": "label", "prompt": "explicit text" }`.
`--limit 0` means all prompts. Add `--resume` to continue an existing checkpoint;
JSON and Markdown are saved to `--output` and its `.md` sibling, or override the
Markdown path with `--markdown-out`. JSON checkpoints are replaced atomically
after each complete baseline/steered pair. An interrupted pair is rerun.

Resume verifies a fingerprint of selected prompts, targets, generation settings,
model directory, local safetensors/config file metadata, package/Python/runtime
versions, research references and the Markdown path. This catches ordinary file replacements and
version changes, not deliberate same-metadata weight changes. Partial resumes
also check the resolved runtime metadata. It rejects changed settings and
malformed/duplicate checkpoint records. Both report paths must be outside the model directory, differ from the prompt
file and each other, and must not be symlinks or hard links to protected files.
A non-resume run rejects existing JSON **or Markdown**. Persistent hidden lock
files prevent concurrent writers to either report; OS locks release on process
exit. Unique temporary files are atomically replaced per report. JSON and
Markdown are not one combined transaction; resume can regenerate stale Markdown.

Current releases use checkpoint **format v2**. Package upgrades participate in
identity checks, so start new report paths when upgrading to 0.2.0. Start a new report when upgrading from v1;
legacy files are not rewritten or resumed. Prompt JSON is limited to 8 MiB and
10000 entries; checkpoint JSON is limited to 128 MiB. Duplicate keys, non-finite
numbers and nesting deeper than 32 levels are rejected.

Text similarity and keyword counts are descriptive signals, not an accuracy,
safety, causal or model-quality score. Read the actual outputs and the recorded
token-budget flag. Steering can change coherence, style and factual reliability.
The generated model has no actions or tools in this application.

## Method and limits

For each target layer `L`, neuron `N` and explicit dose:

```text
scale  = dose * median(norm(residual leaving layer L, prompt tokens 1..end))
               / norm(W_down[L][:, N])
output = output + scale * W_down[L][:, N]
```

Calibration excludes the first token when multiple prompt tokens exist. A
single-token prompt uses its one token. Standard Linear projections select a
column; GPT-2 Conv1D-style projections select a row because their storage is
transposed. Only supported dense projection layouts work; arbitrary quantized,
MoE, multimodal and custom-code architectures are not claimed.

- `--max-tokens`: 1–32768; default 64.
- `--temperature`: finite 0–2; default 0.
- `--seed`: ±100000000; default 0.
- `--context-size`: optional input-plus-completion bound, also limited by known
  model configuration. A larger value does not expand the model's context.
- `--thinking`: leave template thinking enabled. Otherwise it is disabled when
  the template advertises `enable_thinking`.
- `--timeout`: total command budget including stdin and child, 1–86400 seconds;
  default 900. Timeout/cancellation cleans up the worker process group.

Prompts are limited to 10000 characters / 40000 UTF-8 bytes. Child stdout/stderr
are bounded independently to 8 MiB. Standard streams carry JSON requests and
responses; model loading diagnostics go to stderr. Offline loading flags are
library configuration, not an operating-system network sandbox. No credentials,
background service, server or model downloader is configured by this package.

## Verification and provenance

```sh
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
ruff check neuron_steer tests
python -m pip wheel . --no-deps --no-build-isolation -w dist
```

Tests cover validation, dry-run, projection orientation, calibration/hook cleanup,
paired controls, resume identity, output protection, subprocess bounds and context
limits. Optional tests use real torch tensors and skip if torch is absent.
The existing local runtime environment runs those tests without skips, including
nonzero hook math and half-precision overflow rejection.

This package was extracted from the CallAI project's neuron-steering work. The
MIT attribution is retained. `verification/` contains explicitly identified
historical CallAI experiment outputs, plus separate standalone validation results.
See [verification/README.md](verification/README.md). Historical reports do not
establish that every model or target works in this new package.
