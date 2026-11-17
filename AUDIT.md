# Current audit — 0.5.0

Date: 2026-11-17. Scope: persistent local learning and separate artifact export.
Previous research-path fixes remain in [0.4.0](docs/history/audit-v0.4.0.md).

| Concern | Implemented behavior |
| --- | --- |
| Persistent changes could overwrite original weights. | Fresh output directories, source-path guards, cooperative locks and staging publication. |
| Training could update unintended base parameters. | Frozen base, ordinary LoRA A/B-only trainable-parameter check and explicit dense target selection. |
| Prompt tokens could contaminate the supervised objective. | Exact prefix rendering, response/EOS labels only, recorded tensor masks and no truncation. |
| Validation/test leakage could inflate results. | Normalized prompt disjointness and unique final test prompts; semantic independence remains operator responsibility. |
| An adapter could be loaded onto the wrong base. | Content identity of tracked model/tokenizer assets and adapter file-hash checks before loading. |
| Saving files could be mistaken for a working export. | Fresh adapter reload/loss checks and standalone model reload with input/log-probability parity gates. |
| Loss reduction could be mistaken for general quality. | Explicit reference matching/exclusions and no behavioral-improvement certification. |

Regression tests use locally generated GPT-2 and Llama safetensors. They check
training changes, validation loss, untouched source files, response masking,
unknown/tied modules, incompatible/tampered artifacts, disabled/enabled comparison,
failed publication cleanup and standalone export without adapter or steering hooks.
See [verification/adapters-v0.5.0.json](verification/adapters-v0.5.0.json).

## Boundaries

Python 3.11+ and optional training dependencies; one device, batch size one,
dense ordinary LoRA only. No optimizer resume, distributed training, QLoRA or
arbitrary third-party adapter import. Export parity checks selected answer-token
probabilities, not complete behavioral equivalence. SHA256 manifests are not
signatures or protection from hostile local file races. Hard kills may leave
unpublished hidden staging directories. Large-model training and MPS/CUDA memory
fit require separate validation. See [ADAPTERS.md](ADAPTERS.md) for details.
