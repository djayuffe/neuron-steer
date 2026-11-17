# Current audit — 0.4.0

Date: 2026-11-16. This expansion addresses the missing target-discovery workflow.
Earlier correctness fixes remain documented in [0.3.0](docs/history/audit-v0.3.0.md).

| Gap | Implemented behavior |
| --- | --- |
| Users had to guess projection coordinates. | Static/loaded inspection reports supported layers, dimensions and orientation. |
| No evidence-based way to prioritize native neuron hypotheses. | Bounded prompt activation profiles and optional category contrasts rank projection contributions. |
| Separate tools could interpret GPT-2 coordinates differently. | Inspection, probing and interventions share projection-layout validation. |
| Token-only replay omitted attention masks and token types. | Generation/probe manifests preserve every tokenizer input tensor, dtype and hash; the independent forward check replays all inputs. |
| Candidate scores could be mistaken for behavioral improvement. | Explicit observational labels, recoverable formulas/counts and controlled held-out validation examples. |
| New workflows could omit research context or provenance. | Probe references remain annotations; complete prompt/source/model manifests accompany candidates. |

Real tiny-model tests compare scores/ranks to independently captured projection
inputs, exercise both weight orientations, position policies, context bounds,
missing/failed forwards and hook cleanup. Static tests cover offline inspection,
external templates, malformed config and invalid/category-missing plans. An
installed Qwen3.5-4B MPS probe checks the live path without pretrained downloads.
See [verification/expansion-v0.4.0.json](verification/expansion-v0.4.0.json).

## Boundaries

Probe rankings are observational and dataset/template-dependent. They omit bias,
interactions and causal validation. Loaded inspection checks structure; a real
forward is still needed to prove execution. Native MLP neurons are distinct from
Neuronpedia SAE features. Existing dense-layout, platform, reproducibility and
filesystem/provenance boundaries still apply; see [design.md](design.md).
