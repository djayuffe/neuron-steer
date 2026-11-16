# Maintainer overview

Use [design.md](design.md) as the authoritative algorithm and data-flow explanation.
The [visual guide](VISUAL_GUIDE.md) explains concepts through diagrams and a glossary.
The [README](README.md) owns installation and the common workflow;
[NEURONPEDIA.md](NEURONPEDIA.md) owns API examples. Keep option defaults in the CLI
and update these guides when behavior changes.

| Module | Responsibility |
| --- | --- |
| `cli.py` | Arguments, preflight, bounded stdin, dry-run and worker/result handling. |
| `process.py` | Concurrent pipes, buffer caps, command timeout and process-group cleanup. |
| `_worker.py` | Strict private JSON dispatch; errors from optional runtimes become diagnostics. |
| `jobs.py` | Local validation, baseline/intervention selection, evaluation, path guards, locks and resume identity. |
| `runtime.py` | Local weights/templates/devices, context bounds, decoding and hook lifetime. |
| `steering.py` | Target bounds, projection orientation, calibration and additive hooks. |
| `research.py` | Static/loaded inspection, observational activation profiles and contrast candidate ranking. |
| `provenance.py` | Recoverable experiment manifests, source identity and optional asset hashes. |
| `experiments.py` | Seed/dose trial grids, controls and answer-key matching. |
| `evaluation.py` | Prompt data, descriptive metrics and escaped Markdown. |
| `storage.py` | Strict JSON, bounded reads and atomic per-report replacement. |
| `neuronpedia.py` | Explicit API operations, endpoint/credential/response checks and offline references. |

Imports and dry-runs do not load torch or Transformers. Native baseline generation
also avoids projection discovery/calibration. Evaluation always compares an
unhooked arm with the selected intervention. Explicit Neuronpedia jobs never
construct `LocalRuntime`; local references never construct an HTTP client.

## Development checks

Use Python 3.10+:

```sh
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
ruff check neuron_steer tests
ruff format --check neuron_steer tests
python -m pip wheel . --no-deps --no-build-isolation -w dist
```

Install the wheel into an isolated environment and exercise its CLI outside the
checkout, including bundled prompts. Run optional real-tensor tests in a compatible
runtime environment for hook changes. A bounded synthetic live smoke is useful
when loading/decoding changes; do not substitute a long experiment for targeted
regression tests.

CI covers Linux/macOS with Python 3.10, 3.12 and 3.13, pinned linting, package build
and offline installed-CLI checks. Tensor tests skip when torch is absent; recorded
local checks cover real tensor math. Two additional CPU jobs exercise real
Transformers 4.40.2 and 5.23.0 tiny-model loading, tokenization, hooks and probing. Historical experiments are not reclassified
as current validation. See [AUDIT.md](AUDIT.md) for scope and residual limits.

## Documentation diagram maintenance

Keep command routing, hook lifetime and checkpoint diagrams aligned with source.
Use the palette and accessible titles/descriptions from `VISUAL_GUIDE.md`; labels
should communicate meaning independently of color. Prefer small flowcharts over
large graphs, and explain observational evidence separately from causal claims.
Render changed Mermaid blocks to verify syntax, legible labels and unclipped
layouts. Diagram edits do not require model inference when behavior is unchanged.
