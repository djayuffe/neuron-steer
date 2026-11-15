# neuron-steer

**0.2.1 · Python 3.10+ · macOS/Linux**

Generate with an installed Transformers model, compare baseline and temporary MLP
interventions, and research features through explicitly selected Neuronpedia APIs.
The core CLI needs only Python; live local generation requires optional runtime
packages. Local commands never fetch Neuronpedia references or read private
communication history. Weight files are not modified.

## Install

Choose a supported Python environment first; the examples call it `python`.
An older interpreter is rejected rather than bypassing the package requirement.

```sh
python -m pip install -e .               # core CLI, dry-runs, Neuronpedia client
python -m pip install -e '.[runtime]'    # optional torch/Transformers/safetensors
neuron-steer --help
```

Installing missing runtime packages can download dependencies. Loading a model
requires an existing local safetensors checkpoint/tokenizer; the application never
downloads model or SAE weights. Ollama/GGUF/MLX checkpoints are not directly loaded.
Use the runtime versions required by your model; minimum dependencies do not imply
support for every architecture or hardware configuration.

## Preview or run local generation

```sh
# Preview without model loading, report writes or network requests.
neuron-steer generate --model /path/to/model --baseline --prompt 'Hello' --dry-run

# Unhooked generation with explicit stdin.
printf '%s' 'Write a short greeting.' | neuron-steer generate \
  --model /path/to/model --baseline --stdin --device mps --max-tokens 32

# Preview an explicit intervention. Repeat --neuron for up to eight targets.
neuron-steer generate --model /path/to/model --neuron 0:0:0 \
  --prompt 'Write a short greeting.' --dry-run
```

`--baseline` and `--neuron` are mutually exclusive. A zero-dose target calibrates
and installs a zero-vector hook; `--baseline` installs no hooks. Nonzero targets
must be chosen for the exact model/projection layout. Native MLP coordinates and
Neuronpedia SAE coordinates are not interchangeable.

## Compare paired outputs

```sh
neuron-steer evaluate --model /path/to/model --neuron 0:0:0 \
  --limit 2 --max-tokens 32 --output results/control.json --timeout 300
```

The example is a zero-dose control. Every prompt has an unhooked baseline and a
selected intervention, with matched settings and seed. The model loads once per
evaluation. The default is the bundled 100-prompt set; `--prompts FILE` chooses a
JSON list of `{ "category": "label", "prompt": "explicit text" }` records.
`--limit 0` means all prompts. Results include full texts, descriptive metrics,
calibration and token-budget flags; keyword counts are not quality or safety scores.

JSON checkpoints are saved after complete pairs, with a Markdown report alongside.
`--resume` requires the existing compatible checkpoint; it never silently starts a
new run. Changed settings, references, package/runtime versions or tracked model
assets require new report paths. Existing reports remain untouched on validation
failure. A complete resume can rebuild Markdown without loading a model.

Use `--dry-run` to check prompt parsing, selected count and output paths. It does
not load weights, validate model dimensions or fully validate a resume checkpoint.
See [design.md](design.md) for path protection, locks, identity and recovery limits.

## Neuronpedia research

```sh
neuron-steer neuronpedia models
neuron-steer neuronpedia feature \
  --url https://www.neuronpedia.org/gemma-2-2b/20-gemmascope-res-16k/10956
```

The client supports discovery, explanation search, activation/top-feature tests,
service-side steering and offline feature links. Text operations require explicit
stdin, and hosted text may be retained by the service. Add `--dry-run` to inspect
requests locally. Local `generate`/`evaluate` accept optional `--reference URL`
annotations; these URLs are never fetched or applied as targets.

[NEURONPEDIA.md](NEURONPEDIA.md) contains API examples, credentials and self-hosting.

## Documentation and verification

| Document | Purpose |
| --- | --- |
| [design.md](design.md) | Detailed steering math, tensor shapes, local/API data flow, limits and checkpoint semantics. |
| [NEURONPEDIA.md](NEURONPEDIA.md) | Research API workflow and transport boundaries. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Maintainer module map and verification commands. |
| [AUDIT.md](AUDIT.md) | Current fixes, evidence and remaining limits. |
| [CHANGELOG.md](CHANGELOG.md) | Release and compatibility history. |
| [verification/README.md](verification/README.md) | Provenance of standalone checks versus historical experiments. |

Temporary hooks can change style, coherence and factual reliability. No generated
model has actions or tools in this application. Offline loading flags are library
configuration, not an OS network sandbox. Supported layouts and exact resource
bounds are documented in the design; universal model compatibility is not claimed.

Extracted from CallAI's steering experiments as an independent MIT-licensed
package. The original attribution and historical experiment artifacts are retained.
