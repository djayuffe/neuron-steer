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
