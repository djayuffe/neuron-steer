# Current audit — 0.3.0

Date: 2026-11-16. Scope: prompt fidelity, real tokenizer errors, hook execution,
experiment provenance, evaluation controls and regression checks. This records
reproduced issues and their fixes; universal model correctness is not established.

| Finding | Implemented behavior |
| --- | --- |
| Rendered chat templates could receive a second BOS token. | Tokenize rendered templates without adding special tokens; real tokenizer regression. |
| Actual Jinja system-role rejection bypassed fallback. | Explicit role policies and recognized role-error fallback; unrelated/syntax errors propagate. |
| Dictionary template keys hid thinking controls in template values. | Resolve named/default template before detecting and passing the thinking flag. |
| Hook installation was reported as successful application. | Per-target execution counters; fail unused hooks and always clean up. |
| Output reports omitted recoverable settings/template defaults. | Full experiment and per-generation manifests, exact token IDs and effective decoding configuration. |
| Editable source/system-policy changes could mix resumed experiments. | Source and system hashes in v3 identity; optional asset content hashes and shard-index validation. |
| Single uncontrolled comparisons could imply unsupported improvements. | Repeated seeds, dose sweeps, alternating main-arm order and four explicit control families. |
| Text heuristics could be mistaken for correctness measurements. | Operator answer keys, truncation-aware scoring, explicit denominators and prompt-cluster uncertainty. |
| macOS process-group EPERM could mask output-limit errors. | Fall back to owned-child termination, bounded reaping and unconditional pipe cleanup; regression tests. |
| Forced EOS at the token limit hid possible truncation. | Conservative budget flag regardless of final EOS. |
| Model generation defaults could return multiple beams/sequences silently. | Explicit single-beam/single-output policy; retain and record other effective defaults. |

The suite includes real tokenizer/Jinja and tiny random GPT-2 safetensors tests,
without pretrained downloads. It covers BOS fidelity, role handling, named
thinking templates, GPT-2 projection orientation, hook execution/cleanup, random
norm controls, manifests, v3 resume, answer keys and exclusions. See
[verification/correctness-v0.3.0.json](verification/correctness-v0.3.0.json) for measured checks.

## Remaining boundaries

Supported dense projection layouts only; hosted/GGUF/MLX engines do not expose
these hooks. Asset metadata mode is weaker than content hashes; neither creates
signed provenance or protects hostile filesystem races. Reports are independently
atomic. Exact behavioral reproducibility varies with runtime/hardware. Answer
matching checks supplied keys, not semantic truth; small datasets, correlated
seeds and exploratory confidence intervals can mislead. More models and curated
benchmarks require separate validation. Offline loading flags are library settings,
not an OS network sandbox. Neuronpedia references remain annotations; explicit API
operations have their documented transport and submission behavior.

Earlier audit fixes remain in place: [0.2.1](docs/history/audit-v0.2.1.md) and
[through 0.2.0](docs/history/audit-through-0.2.0.md).
