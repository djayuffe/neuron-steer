# Verification provenance

`neuron-steering-*` files were copied from CallAI's existing local experiments.
They are historical artifacts, not new standalone executions. Their model paths,
version labels, controls, target choices and limitations remain as recorded. Raw
model outputs are untrusted generated text and may be incoherent or objectionable.
No source weight files or personal communication history are included.

`standalone-*` files describe checks performed for this independent package.
The live smoke test uses installed local weights and a zero-dose control target;
it verifies loading, CLI/worker exchange, calibration and cleanup without claiming
that a particular nonzero target improves behavior. Unit tests additionally check
nonzero hook math with synthetic objects and real torch tensors.

`correctness-v0.3.0.json` records the 85-test correctness pass, nine real
Transformers regressions, a 48-generation tiny-model CLI/resume experiment and
an installed Qwen zero-dose MPS smoke. Those checks establish execution properties,
not improved behavior or universal model compatibility.

`expansion-v0.4.0.json` records 99 local tests, independent real-forward probe
math checks, installed-wheel inspection/probing and a Qwen3.5-4B activation probe.
Candidate ranks are observations and do not establish behavioral improvement.

`visual-docs-2026-11-16.json` records 26 rendered Mermaid blocks (18 distinct
diagrams), 54 checked local links/anchors, accessible SVG titles/descriptions
and light/dark layout review. These are documentation checks; runtime source
was unchanged. See [the visual guide](../VISUAL_GUIDE.md).
