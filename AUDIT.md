# Current audit — 0.2.1

Date: 2026-11-15. Scope: CLI, local runtime selection, checkpoint identity,
JSON boundaries, Neuronpedia regression coverage and documentation. This records
concrete fixed issues; it is not a guarantee that all possible failures are known.

| Finding | Implemented behavior |
| --- | --- |
| Invalid local options could block while waiting for stdin. | Scalar, target and reference preflight runs before stdin. |
| Missing resume files silently started a fresh evaluation. | `--resume` requires an existing JSON checkpoint; validation fails before model loading. |
| Tokenizer files and nested weight shards were absent from identity. | Recursively track safetensors and known config/tokenizer/vocabulary assets by relative name, size and mtime. |
| Strict JSON accepted overflow numbers and unpaired surrogates. | Reject exponent overflow, invalid Unicode keys/values, duplicates, non-finite values and excess depth. |
| Optional checkpoint metadata and format versions accepted wrong types. | Input-token counts, budget flags and version integers are validated. |
| Unhooked standalone generation required a dummy neuron coordinate. | `generate --baseline` needs no target and never installs calibration/steering hooks. |
| Dry-run evaluation did not check its actual inputs/outputs. | Validate prompt files, count limits and report paths; no weights or output files are loaded/created. |
| Report directory/device paths produced late filesystem errors. | Reject non-file output targets explicitly. |
| Programmatic jobs and CLI symlink loops used inconsistent path handling. | Canonicalize model/input paths before output guards; preserve output symlink checks and return clean CLI path diagnostics. |
| Guides repeated material and mixed historical claims with current behavior. | Short README/maintainer overview; detailed design and API guide remain canonical. Historical audits are preserved separately. |

Tests reproduce these failures and cover baseline dispatch, offline references,
Neuronpedia contracts/loopback transport, real-tensor math, overflow prevention,
hook cleanup, checkpoint locks, atomic writes and report protection. Exact checks
and counts are recorded in [verification/polish-v0.2.1.json](verification/polish-v0.2.1.json).

## Remaining boundaries

- macOS/Linux only; the worker and report locks use POSIX APIs.
- Metadata fingerprints are not full weight hashes or signed provenance.
  Hidden files and symlinked model directories are not recursively fingerprinted.
- Report guards/locks protect cooperative runs, not hostile local filesystem races.
  JSON and Markdown are independently atomic; JSON is the recovery checkpoint.
- A dry-run does not load weights, prove target dimensions, check available memory
  or guarantee resume compatibility. See [design.md](design.md).
- Offline loading is library configuration, not an OS network sandbox. Explicit
  Neuronpedia operations can submit/store text; local references never do.
- Supported dense projection layouts only. Model effects and seeded reproducibility
  vary by architecture, runtime and hardware; text metrics are descriptive.

[Historical findings through 0.2.0](docs/history/audit-through-0.2.0.md) retain the
original audit provenance. [CHANGELOG.md](CHANGELOG.md) records compatibility changes.
