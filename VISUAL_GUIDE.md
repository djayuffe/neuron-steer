# Understand neuron-steer

<a id="a-visual-guide-to-neuron-steer"></a>

**Explore a model. Try a temporary nudge. Compare what changes.**

This guide starts with everyday explanations. Each topic follows the same order:
**ELI5 → picture → key point → optional technical details**. You can read the
short version first and open the details when you need them.

[Install and try it](README.md#install) · [Runnable examples](README.md#examples) ·
[Glossary](#16-glossary-and-a-worked-experiment) · [Technical design](design.md)

## Choose your reading path

| If you want to… | Start here |
| --- | --- |
| Understand the idea in a few minutes | [The basic idea](#1-the-basic-idea) → [Choose a command](#2-choose-a-command) → [The workflow](#3-the-complete-research-workflow) |
| Understand what happens inside the model | [Text inputs](#4-from-text-to-model-inputs) → [Coordinates](#5-what-a-neuron-coordinate-means) → [Dose](#6-dose-and-calibration) → [Hooks](#7-temporary-hook-lifetime) |
| Plan a useful experiment | [Probing](#8-how-probing-finds-candidates) → [Controls](#9-seeds-dose-sweeps-and-controls) → [Scoring](#10-what-the-scores-establish) |
| Understand saved results and integrations | [Manifests](#11-manifests-and-reproducibility) → [Resume](#12-saving-and-resuming-experiments) → [Neuronpedia](#13-neuronpedia-and-coordinate-systems) |

<details>
<summary>Show the color key and all topics</summary>

### How to read the colors

| Color | Meaning | Typical example |
| --- | --- | --- |
| Blue | Inputs or a question | An explicit prompt or installed checkpoint |
| Teal | Local computation | Tokenization, probing, generation |
| Amber | An experiment choice or intervention | A dose, branch or selected target |
| Purple | Results and recorded evidence | Manifests, scores, candidate profiles |
| Pink | Explicit service-side research | Neuronpedia API operations |
| Red | A limit, exclusion or failure | Context overflow or incompatible resume |

Node labels and arrow captions carry the meaning as well as color. Diamonds are
decisions; rectangles are actions or records. Solid arrows show data/control flow;
dotted arrows show a research relationship or an operator's next iteration.
Each diagram has an accessible title and description. If your Markdown viewer
shows source code, use this document on GitHub or a Mermaid-enabled preview.

### Find the concept you need

1. [The basic idea](#1-the-basic-idea)
2. [Choose a command](#2-choose-a-command)
3. [The complete research workflow](#3-the-complete-research-workflow)
4. [From text to model inputs](#4-from-text-to-model-inputs)
5. [What a neuron coordinate means](#5-what-a-neuron-coordinate-means)
6. [Dose and calibration](#6-dose-and-calibration)
7. [Temporary hook lifetime](#7-temporary-hook-lifetime)
8. [How probing finds candidates](#8-how-probing-finds-candidates)
9. [Seeds, dose sweeps and controls](#9-seeds-dose-sweeps-and-controls)
10. [What the scores establish](#10-what-the-scores-establish)
11. [Manifests and reproducibility](#11-manifests-and-reproducibility)
12. [Saving and resuming experiments](#12-saving-and-resuming-experiments)
13. [Neuronpedia and coordinate systems](#13-neuronpedia-and-coordinate-systems)
14. [Preview, runtime and device choices](#14-preview-runtime-and-device-choices)
15. [Bounds and error handling](#15-bounds-and-error-handling)
16. [Glossary and a worked experiment](#16-glossary-and-a-worked-experiment)


</details>

## 1. The basic idea

> **ELI5:** Ask the same storyteller twice: once normally, then once with a small
> temporary nudge. Compare the answers.

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart TB
  accTitle: The same question with and without an intervention
  accDescr: The same prompt gets a usual answer and a temporarily nudged answer. Compare the answers and record what changed.
  Q["Your question"]:::input --> B["Usual answer"]:::local
  Q --> S["Nudged answer"]:::choice
  B --> C["Compare both answers"]:::evidence
  S --> C
  C --> J["Save what changed<br/>and what is still uncertain"]:::evidence
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

**Key point:** A changed answer is an observation. It is not automatically a better
answer.

Imagine a storyteller with a mixing desk inside. A baseline asks the question
with the desk untouched. Steering temporarily adds a signal at a chosen place.
The comparison shows what happened for this question, dose, model and settings.

The saved weights do not change. Changed wording alone does not show improved
accuracy, safety or quality. Read the full answers and use explicit measurements.

## 2. Choose a command

> **ELI5:** Choose the tool that answers your question: look inside, watch activity,
> write text, compare results, or explore research.

| Command | Use it to… |
| --- | --- |
| `inspect` | Read a local model’s configuration and template names without loading weights. |
| `inspect --load` | Load existing weights and check supported layers, widths and projection layouts. |
| `probe` | Rank candidate coordinates from a prompt file and selected layers. |
| `generate --baseline` | Write one response to an explicit prompt without steering. |
| `generate --neuron L:N:d` | Write one response with your chosen temporary intervention. |
| `evaluate` | Compare prompts under paired conditions and save reports/checkpoints. |
| `neuronpedia` | Look up research IDs/features or analyze explicitly supplied text on the service. |

<details>
<summary>Open the visual command map</summary>

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart LR
  accTitle: Choose the command for your question
  accDescr: Inspect checks capabilities, probe ranks observational candidates, generate produces text, evaluate compares trials, and neuronpedia performs explicit research.
  Q{"Choose your goal"}:::input
  Q --> I["inspect<br/>Read model details"]:::local
  I --> IL["Add --load<br/>Check real layer shapes"]:::choice
  Q --> P["probe<br/>Find candidates"]:::local
  P --> H["Candidates to test<br/>You choose the targets"]:::evidence
  Q --> G["generate<br/>Write one response"]:::local
  Q --> E["evaluate<br/>Compare paired trials"]:::evidence
  Q --> N["neuronpedia<br/>Explore feature research"]:::remote
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

</details>

**Key point:** Start with inspect if you do not yet know your model’s supported
layers.


Baseline generation does not require a recognized MLP layout. Native interventions
and probes do. Loaded inspection verifies structure; a successful forward is
needed to verify actual execution.

## 3. The complete research workflow

> **ELI5:** Look first, find ideas to test, then compare them fairly using new
> questions.

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart TB
  accTitle: From installed weights to a controlled experiment
  accDescr: Inspect installed weights, probe discovery prompts, explicitly choose a coordinate and dose, evaluate separate prompts, and review evidence.
  W["Installed model weights"]:::input --> I["inspect --load<br/>Check layer and projection widths"]:::local
  I --> P["probe<br/>Use discovery prompts"]:::local
  P --> H["Ranked hypotheses<br/>Native layer:neuron coordinates"]:::evidence
  H --> D["Choose targets<br/>and trial doses"]:::choice
  D --> E["evaluate<br/>Separate validation prompts + controls"]:::local
  E --> R["Read results<br/>and full answers"]:::evidence
  R -. "Refine the next experiment" .-> D
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

**Key point:** Discovery prompts suggest targets. Separate validation prompts test
those targets.

Use discovery prompts to propose candidates, then separate validation prompts
to test their effects. The program does not automatically pick the best semantic
feature, optimize a dose, train weights or reserve a hidden validation split.
You choose those experimental decisions explicitly.

Neuronpedia can supply research context at any stage. A reference URL does not
change local targets, and a native neuron is not automatically an SAE feature.

## 4. From text to model inputs

> **ELI5:** The model reads numbered text pieces. A template puts your question into
> the format the model expects.

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart TB
  accTitle: Prompt preparation preserves actual model inputs
  accDescr: Resolve a template and role policy, render once, tokenize with the appropriate special-token setting, check context, and preserve every model input.
  P["Explicit prompt + system instruction"]:::input --> T{"Chat template available?"}:::choice
  T -- "Yes" --> N["Resolve named/default template<br/>Apply system-role policy"]:::local
  N --> R["Render once<br/>Set supported thinking flag"]:::local
  R --> C["Tokenize rendered chat<br/>add_special_tokens=False"]:::local
  T -- "No" --> F["Plain System/User/Assistant format<br/>Tokenizer special tokens enabled"]:::local
  C --> B{"Fits the known<br/>token bound?"}:::choice
  F --> B
  B -- "No" --> X["Clear error<br/>No silent trimming"]:::limit
  B -- "Yes" --> M["Save every input<br/>values, types and hashes"]:::evidence
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

**Key point:** Save all actual inputs, including masks and segment labels, so a
comparison can be replayed.

<details>
<summary>Details: text formatting, roles and thinking controls</summary>

A template turns role-labeled messages into the text the model expects.
`--chat-template NAME` selects a named template. `--system-role auto` retries a
recognized system-role rejection with the instruction merged into the user
message; `merge` selects that form directly and `strict` disables the fallback.
Syntax and unrelated template errors propagate.

Rendered chat is tokenized without adding special tokens again, avoiding a second
BOS token. Plain fallback keeps the tokenizer's normal special tokens. The
thinking flag is passed only when the resolved template advertises it; requesting
it is not proof that the model follows it.

A generation budget includes input plus requested completion tokens. A probe
checks its input-only bound. Reports retain complete tokenizer tensors, including
attention masks and token types when present, so replay is not based on IDs alone.

</details>

## 5. What a neuron coordinate means

> **ELI5:** A coordinate is an address inside the model, like a floor and room number.
> It is not the name of an idea.

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart TB
  accTitle: A native neuron selects a projection vector
  accDescr: A layer and neuron coordinate select a column of a Linear weight or a row of a GPT-2 Conv1D weight, both representing a residual-space vector.
  T["Target L:N:d<br/>layer : neuron : dose"]:::input --> L["Find layer L<br/>and its MLP projection"]:::local
  L --> O{"Stored weight layout"}:::choice
  O -- "Linear: residual × MLP" --> C["Column N<br/>v = W[:, N]"]:::local
  O -- "GPT-2 Conv1D: MLP × residual" --> R["Row N<br/>v = W[N, :]"]:::local
  C --> V["Result: direction v<br/>in residual space"]:::evidence
  R --> V
  V --> A["Add a scaled copy of v<br/>to projection output"]:::choice
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

**Key point:** The address selects a direction from this model’s weights; it does not
identify a universal concept.

<details>
<summary>Details: coordinate meaning and tensor layout</summary>

`L:N:d` means decoder layer **L**, projection input coordinate **N**, and signed
**dose d**. For a standard Linear layer, column N describes the residual-space
contribution associated with that MLP coordinate. GPT-2 stores the same kind of
mapping transposed, so the implementation selects a row.

If the MLP input activation at coordinate N is `a[N]`, its linear contribution is
`a[N] × v`. Steering adds a separate calibrated multiple of `v` to the output.
It does not directly overwrite `a[N]`, assign a semantic concept, or include the
projection bias in the selected vector.

Dense floating-point weights and supported layouts are required. Actual loaded
widths determine valid coordinates within the parser's outer bounds.

</details>

## 6. Dose and calibration

> **ELI5:** Dose is a volume knob. Calibration measures the current signal so the knob
> has a useful reference level.

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart TB
  accTitle: A worked example of prompt-relative dose
  accDescr: Median residual norm six, projection norm two and dose one half give scale one point five and injected norm three.
  M["Prompt residual norm<br/>m = 6"]:::input --> S["scale = dose × m / norm(v)<br/>0.5 × 6 / 2 = 1.5"]:::choice
  V["Projection vector norm<br/>norm(v) = 2"]:::input --> S
  D["Signed dose<br/>dose = +0.5"]:::input --> S
  S --> A["Injection = 1.5 × v<br/>Injection norm = 3"]:::local
  A --> O["Add at projection output<br/>during prompt processing and decode"]:::choice
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

**Key point:** A dose controls how much signal is added, not how good the answer will
be.

<details>
<summary>Details: calibration policy, formula and rounding</summary>

First run the prompt without steering. At each selected decoder block, measure
residual-vector norms and take their median: `m`. The default `skip-first`
calibration excludes position zero for multi-token prompts; `all` includes it.
A single-token prompt uses its only position. This is a positional policy, not a
BOS-token detector. PyTorch's median uses the lower middle value for an even count.

The rule is `injection = dose × m / norm(v) × v`. Thus injection norm is
approximately `abs(dose) × m`, after dtype rounding. Negative doses reverse the
addition; zero doses still calibrate and install zero-vector hooks. An unhooked
baseline does neither.

Multiple targets add vectors, and their directions can reinforce or cancel.
Their combined norm need not equal the sum of individual norms. Calibration is
fixed for one generation; it does not adapt after every generated token. Invalid
norms, non-finite values and destination-dtype overflow produce errors.

</details>

## 7. Temporary hook lifetime

> **ELI5:** A hook is a temporary instruction: whenever this part runs, watch it or
> add the chosen signal. Remove the instruction afterward.

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  sequence:
    actorFontFamily: Arial, Helvetica, sans-serif
    messageFontFamily: Arial, Helvetica, sans-serif
    noteFontFamily: Arial, Helvetica, sans-serif
    actorFontSize: 18
    messageFontSize: 18
    noteFontSize: 18
    mirrorActors: false
---
sequenceDiagram
  accTitle: Temporary hook lifetime during steered generation
  accDescr: Calibration observers are removed before intervention hooks are installed. Generation records actual hook calls and always removes intervention handles on normal Python return or error.
  autonumber
  participant R as Runtime
  participant M as Local model
  participant H as Hook records
  rect rgba(37, 99, 235, 0.12)
    R->>M: Install calibration observers
    R->>M: Run unsteered prompt forward
    M-->>R: Residual norms at selected layers
    R->>M: Remove calibration observers
  end
  rect rgba(245, 158, 11, 0.12)
    R->>R: Compute and validate fixed injection vectors
    R->>M: Install temporary intervention hooks
    R->>R: Reset generation seed
    R->>M: Generate with recorded settings
    loop Every executed selected projection
      M->>H: Add vector, count calls and positions
    end
  end
  rect rgba(124, 58, 237, 0.12)
    R->>H: Verify every target executed
    H-->>R: Calls, positions and norms
    R->>M: Finally remove intervention hooks
    Note over R,M: Cleanup also runs on Python errors
  end
```

**Key point:** Installed means attached. Applied means the selected part actually ran.

<details>
<summary>Details: execution evidence and cleanup</summary>

A hook is a temporary callback attached to a module execution. Calibration
observers collect norms. Intervention hooks then add fixed vectors during prompt
processing inside generation and subsequent decode calls. Reports distinguish
**installed** from **applied**: every requested target must actually execute.
Call and position counts show what ran; they are not a behavioral success score.

Python returns and exceptions remove handles in `finally`. A hard worker kill
instead discards the process and its memory; it does not promise that Python
cleanup code ran. Either way, source weight files are not edited.

</details>

## 8. How probing finds candidates

> **ELI5:** Watch which indicator lights change for different questions. Use those
> observations to choose what to test next.

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart TB
  accTitle: How observational probing ranks candidates
  accDescr: Unmodified forwards expose projection input activations. Position selection and equal-prompt weighting lead to contribution magnitude or a signed category contrast, then a ranking to validate.
  P["Explicit discovery prompts<br/>Optional positive/negative categories"]:::input --> F["Unmodified prompt forwards<br/>No generation or intervention"]:::local
  F --> A["Observe input activations<br/>Select token positions"]:::local
  A --> M["Average within each prompt<br/>Weight prompts equally"]:::local
  M --> C{"Category contrast requested?"}:::choice
  C -- "No" --> U["Mean absolute activation<br/>× vector norm"]:::evidence
  C -- "Yes" --> S["Absolute category difference<br/>× vector norm"]:::evidence
  U --> K["Top candidates per layer<br/>Scores and recorded inputs"]:::evidence
  S --> K
  K --> V["Test on separate validation prompts<br/>with explicit doses and controls"]:::choice
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

**Key point:** A high probe score is a candidate to investigate, not proof of a useful
intervention.

<details>
<summary>Details: scoring formulas, weighting and limits</summary>

A probe observes projection **inputs**, rather than changing them. Forwards use
evaluation/inference mode, disabled KV caching, and seed zero. No tokens are generated. The default
`last` position is the last formatted input token, often an assistant prefix.
`all` averages every position; `skip-first` excludes position zero when possible.
Each prompt has equal weight regardless of length.

Without category contrast, score is `mean_p(mean_t(abs(a[p,t,j]))) × norm(v[j])`.
With contrast, compute signed per-prompt means, average within each category,
subtract negative from positive, then rank `abs(difference) × norm(v[j])`.
The report preserves the sign separately from the ranking magnitude. Ties use
ascending neuron coordinates.

A large score is an observed contribution or category difference. It does not
identify a truthful neuron or prove which steering sign improves a response.
Prompt wording, template tokens and category imbalance can influence discovery.
The implementation returns top candidates and aggregates rather than full
activation tensors. Bounds: 64 selected prompts, 1–8 layers, up to 64 candidates
per layer, and up to 4,096 input tokens per prompt (512 by default).

</details>

## 9. Seeds, dose sweeps and controls

> **ELI5:** Compare the chosen nudge with no nudge, a zero nudge, a random nudge, and
> other checks. Start each paired run with the same dice setup.

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart LR
  accTitle: An evaluation trial expands into explicit arms
  accDescr: Prompt, seed and dose scale define one trial. It contains the two main arms and any requested controls; all arms complete before the trial is checkpointed.
  P["Prompt × seed × dose scale<br/>One paired trial"]:::input --> B["Baseline<br/>No hooks"]:::local
  P --> S["Steered<br/>Chosen targets"]:::choice
  P --> Z["Zero control<br/>Same targets, dose zero"]:::local
  P --> R["Random control<br/>Match each vector norm"]:::local
  P --> V["Reversed control<br/>Negate all doses"]:::local
  P --> A["Ablation controls<br/>Leave out one target at a time"]:::local
  B --> J["All conditions finished<br/>Save the trial"]:::evidence
  S --> J
  Z --> J
  R --> J
  V --> J
  A --> J
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

**Key point:** A trial contains all requested conditions for one question, seed and
dose scale.

<details>
<summary>Details: control arms, seeds and generation counts</summary>

A **trial** is one prompt at one seed and one dose scale. The same seed is reset
before every arm, after calibration, so paired sampling starts from the same RNG
state. Hardware/library changes can still alter results. Repeated seeds in greedy
decoding do not create independent behavioral evidence.

| Arm | Purpose | Interpretation limit |
| --- | --- | --- |
| Baseline | Observe the unhooked model | It has different execution costs from calibrated arms |
| Steered | Test selected vectors and doses | A change is not automatically an improvement |
| Zero | Exercise zero-vector hooks | It still runs calibration |
| Random | Compare norm-matched random directions | Individual norms match; combined multi-target norms may differ |
| Reversed | Reverse the selected directions | Opposite sign need not produce an opposite semantic effect |
| Ablation | Leave one target out in each control | With one target, the omitted-target arm is unhooked |

Only baseline and steered are mandatory. `--order alternate` swaps their order
on every second trial; requested controls run afterward. `baseline-first` fixes
the main-arm order. It does not fully randomize all arms.

Example: **3 prompts × 3 seeds × 3 scales = 27 trials**. With one target and all
four control families, each trial has six arms: **162 generations**. For multiple
targets, ablation adds one arm per target. Planning is capped at 10,000 trials
and 100,000 generations; use `--dry-run` to inspect the actual count.

</details>

## 10. What the scores establish

> **ELI5:** Check answers against your answer sheet. Keep track of how many answers
> were scored and how many may have been cut short.

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart TB
  accTitle: What answer-key scoring includes and excludes
  accDescr: All records remain available for descriptive review. Primary answer-key accuracy uses labeled pairs where neither main output reached the token budget; uncertainty uses prompt clusters.
  R["All completed trial records"]:::input --> D["Descriptive review<br/>Text changes, timing, keywords, full outputs"]:::evidence
  R --> B{"Either main arm reached<br/>the token budget?"}:::choice
  B -- "Yes, including forced EOS at limit" --> X["Keep the record<br/>Exclude from primary answer-key accuracy"]:::limit
  B -- "No" --> K{"Operator answer key available?"}:::choice
  K -- "No" --> U["Unscored complete pair"]:::evidence
  K -- "Yes" --> M["Match raw decoded text<br/>exact or whitespace/case normalized"]:::local
  M --> A["Paired accuracy delta<br/>Explicit denominator + seed/dose groups"]:::evidence
  A --> C{"At least two scored<br/>prompt clusters?"}:::choice
  C -- "Yes" --> I["1,000 prompt-cluster bootstrap samples<br/>Exploratory 95% interval"]:::evidence
  C -- "No" --> N["No bootstrap interval"]:::evidence
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

**Key point:** Matching this answer key does not prove that the model is generally
truthful.

<details>
<summary>Details: answer matching, exclusions and uncertainty</summary>

`content` is decoded text with edge whitespace stripped. `raw_content` preserves
that whitespace and is used for operator answer-key matching. Exact matching
compares the entire raw string; normalized matching collapses whitespace and
casefolds. Neither uses substring guesses or a semantic AI judge.

Either main arm reaching its token budget excludes the pair from primary
answer-key accuracy, including when EOS was forced at the limit. The report
retains those outputs and includes them in all-record descriptive metrics.
Exclusion is conservative: it signals possible truncation, not a proven failure.

Summaries expose denominators, baseline/steered accuracy, paired delta, per-prompt
macro delta, seed/dose groups and control comparisons. With at least two scored
prompt clusters, a deterministic 1,000-replicate bootstrap estimates an exploratory
95% interval over prompt-cluster means. It has no multiple-comparison correction.
Repeated seeds for the same prompt are clustered rather than treated as wholly
independent questions. Supplied answer keys can be wrong; an interval or keyword
count does not establish general truthfulness, safety or causal attribution.

</details>

## 11. Manifests and reproducibility

> **ELI5:** Keep both the recipe and the receipt: what you planned and what the
> computer actually used.

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart LR
  accTitle: Reproducible reports have two complementary records
  accDescr: The experiment manifest identifies data, source, assets and settings, while each generation records actual formatted inputs, decoding configuration and hook execution evidence.
  E["Experiment manifest"]:::input --> A["Prompt data + answer keys + references<br/>targets, seeds, doses, controls, order"]:::evidence
  E --> S["Source hashes + system instruction<br/>package/runtime versions + asset identity"]:::evidence
  G["Per-generation manifest"]:::input --> P["Rendered prompt + resolved template<br/>all input tensor values, dtypes and hashes"]:::evidence
  G --> C["Effective generation configuration<br/>output token IDs + inferred stop status"]:::evidence
  G --> H["Hook execution evidence<br/>calls, positions and injection norms"]:::evidence
  A --> J["Recoverable JSON report<br/>Inspect the evidence behind a claim"]:::local
  S --> J
  P --> J
  C --> J
  H --> J
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

**Key point:** Recorded inputs and settings make differences easier to investigate;
they do not promise identical results everywhere.

<details>
<summary>Details: identity, hashes and reproducibility</summary>

Think of a manifest as an experiment recipe plus its actual execution record.
The top-level recipe describes the dataset, intervention, source and environment.
Per-generation records describe the exact formatted inputs and inherited decoding
settings that were actually used. Probe records likewise retain their inputs,
position counts and ranking policy.

Default asset identity uses relative names, sizes and mtimes for tracked files.
`--asset-identity sha256` also hashes their content, which takes time for large
weights and shares the command deadline. Source files are hashed separately.
Metadata mode cannot detect every edit; neither mode signs the report or prevents
hostile local tampering. A manifest supports investigation and compatible resume;
it does not guarantee bitwise equality on another device or runtime.

</details>

## 12. Saving and resuming experiments

> **ELI5:** Save progress after a whole round. If a round is interrupted, keep the
> finished rounds and redo the unfinished one.

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart TB
  accTitle: Checkpoint recovery preserves complete trials
  accDescr: Resume requires a compatible existing v3 checkpoint. Completed trials are reused, incomplete trials rerun, and Markdown can be rebuilt from complete JSON.
  R["New run or --resume"]:::input --> G["Check report paths and acquire locks"]:::local
  G --> Q{"Resume requested?"}:::choice
  Q -- "No" --> N["Fresh report paths<br/>Load model; start trial"]:::local
  Q -- "Yes" --> V{"Saved report<br/>compatible?"}:::choice
  V -- "No" --> X["Fail clearly<br/>Choose fresh paths or correct settings"]:::limit
  V -- "Yes" --> C{"Every trial already complete?"}:::choice
  C -- "Yes" --> M["Rebuild Markdown from JSON<br/>No model loading needed"]:::evidence
  C -- "No" --> T["Load model; compare runtime metadata<br/>Skip completed trials"]:::local
  T --> U["Rerun the unfinished trial<br/>including all its controls"]:::local
  N --> S["After a complete trial<br/>Atomically replace JSON"]:::evidence
  U --> S
  S --> F["At completion<br/>Write final summary and Markdown"]:::evidence
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

**Key point:** An experiment checkpoint saves results and progress. It is not a newly
trained model.

<details>
<summary>Details: resume validation and atomic reports</summary>

JSON is the recovery checkpoint. It is written only after a whole trial and its
requested controls finish. If interrupted mid-trial, resume keeps earlier complete
trials and reruns that partial trial. JSON and Markdown are independently atomic;
a crash can leave newer JSON and older Markdown.

`--resume` requires an existing compatible **v3** checkpoint. Data, references,
source/system policy, model asset identity, package/runtime settings and report
paths participate in identity; partial resumes also compare loaded runtime
metadata. Changed identity requires new paths. Old v1/v2 checkpoints cannot resume,
and a package/source change such as 0.3.0 to 0.4.0 also requires fresh paths.

Report paths are checked against model/input files and each other, reject symlink
outputs, and acquire non-blocking advisory locks. New runs refuse existing report
files. These protections cover cooperative local use, not hostile filesystem races.

</details>

## 13. Neuronpedia and coordinate systems

> **ELI5:** Neuronpedia is a library of feature research. Its feature addresses and
> your model’s native neuron addresses use different maps.

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart TB
  accTitle: Local annotations and explicit API research have different data paths
  accDescr: Local references are saved without HTTP. Explicit Neuronpedia operations may contact the service and text operations submit only explicit stdin. SAE feature coordinates are not local native-neuron coordinates.
  U["Neuronpedia feature URL"]:::input --> Q{"Chosen use"}:::choice
  Q -- "--reference on a local job" --> A["Validate and store annotation<br/>No HTTP request"]:::local
  A --> J["Local generate / evaluate / probe<br/>Native coordinates chosen explicitly"]:::evidence
  Q -- "neuronpedia link" --> L["Return dashboard URL<br/>No HTTP request"]:::local
  Q -- "Explicit API operation" --> R["models / model / source / feature<br/>or search / activate / top / steer"]:::remote
  R --> D{"Text operation?"}:::choice
  D -- "No" --> M["Request metadata or dashboard data"]:::remote
  D -- "Yes, explicit --stdin required" --> T["Submit only supplied text<br/>Service may retain it"]:::remote
  M --> O["Bounded API JSON response"]:::evidence
  T --> O
  O -. "Human research context only" .-> J
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

**Key point:** A saved reference is a note. It does not download a vector or choose a
local target.

<details>
<summary>Details: SAE coordinates, API operations and data handling</summary>

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart TB
  accTitle: Native neurons and SAE features are different coordinate systems
  accDescr: Native neuron coordinates select a model MLP projection vector. An SAE latent index requires a specific decoder, model and hook. Equal numbers do not imply equal directions.
  N["Native coordinate L:N"]:::input --> V["Installed model MLP projection<br/>Linear column or Conv1D row"]:::local
  V --> R["Native residual-space direction"]:::evidence
  S["SAE coordinate<br/>model + source + feature index"]:::input --> D["Specific SAE decoder<br/>and aligned model hook"]:::choice
  D --> H["Direction in that SAE hook's space"]:::evidence
  R -. "Cannot infer a mapping" .-> X["Equal index numbers<br/>do not imply equal vectors"]:::limit
  H -. "Alignment is required" .-> X
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

A **native MLP neuron** is an input coordinate of the installed model's MLP output
projection. An **SAE feature** is a latent coordinate of a particular sparse
autoencoder attached at a particular model/hook. Even equal index numbers can
mean entirely different directions. Correct SAE steering would require the right
decoder, model and hook alignment; this implementation does not perform that mapping.

`models`, `model`, `source` and `feature` retrieve public research resources.
`search` searches explanations; `activate` requests feature activations; `top`
requests prominent features; `steer` requests service-side steering. The four text
operations require explicit `--stdin`. `link` constructs a dashboard URL offline.
Any API operation with `--dry-run` previews the plan without making its request.

Remote API access uses the allowed official HTTPS origin, verified certificates,
bounded responses and optional credentials from a named environment variable.
A configured loopback Neuronpedia webapp is also supported. The package does not
start services; a loopback gateway can itself forward externally. Hosted text may
be retained or shared. Local reference annotations never fetch the URL, submit
text, or install a feature vector. See [the API guide](NEURONPEDIA.md) for payloads,
self-hosting, limits and concrete examples.

</details>

## 14. Preview, runtime and device choices

> **ELI5:** A preview checks the plan. A real run opens the model and asks an
> available CPU or GPU to do the work.

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart TB
  accTitle: Runtime and preview modes have distinct costs
  accDescr: Dry-run avoids loading weights. Static inspection reads configuration, loaded inspection and local forwards need optional runtimes. Auto device selection prefers CUDA then MPS then CPU.
  O["Requested operation"]:::input --> D{"--dry-run?"}:::choice
  D -- "Yes" --> P["Validate supported options and selected inputs<br/>No model loading, generation or API request"]:::evidence
  D -- "No" --> T{"Execution path"}:::choice
  T --> I["inspect without --load<br/>Read bounded local configuration"]:::local
  T --> L["Loaded inspect / probe / generate / evaluate<br/>Use installed safetensors and tokenizer"]:::local
  T --> N["Explicit Neuronpedia API<br/>No local model or torch required"]:::remote
  L --> G{"--device auto"}:::choice
  G -- "CUDA available" --> C["CUDA"]:::local
  G -- "Otherwise MPS available" --> M["MPS"]:::local
  G -- "Otherwise" --> B["CPU"]:::local
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

**Key point:** Previewing a plan does not prove the model fits in memory or supports
the chosen coordinate.

<details>
<summary>Details: runtimes, devices and previews</summary>

Core imports, static inspection, dry-runs and the API client need no torch or
Transformers. Loaded inspection and local forwards need the optional runtime and
an existing local safetensors checkpoint/tokenizer. Offline loading is library
configuration with remote model code disabled, not an OS network sandbox.

`--device cpu`, `mps` and `cuda` explicitly select a device. `auto` prefers CUDA,
then available MPS, then CPU. Availability, enough memory, supported model code
and compatible library versions remain execution requirements.

Dry-run checks operation-specific plans. Evaluation/probe previews validate their
selected prompt inputs; they do not load weights, prove dimensions or guarantee
that the model fits. Static inspection reads configuration claims only. Loaded
inspection checks structure; probe/generation supply execution evidence.

</details>

## 15. Bounds and error handling

> **ELI5:** Give the job a timer and size limits. If it fails, stop the temporary work
> and return a clear explanation.

```mermaid
---
config:
  fontFamily: Arial, Helvetica, sans-serif
  fontSize: 18
  themeVariables:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 18px
  flowchart:
    nodeSpacing: 28
    rankSpacing: 42
    padding: 14
---
flowchart TB
  accTitle: A command has bounded work and explicit failure paths
  accDescr: Validation, subprocess deadlines, stream caps, strict JSON, hook cleanup and protected reports bound execution. Forceful process termination discards worker memory without changing weight files.
  C["CLI validates explicit inputs<br/>before waiting for stdin"]:::input --> W["Worker process<br/>One request deadline + bounded pipes"]:::local
  W --> F{"Runtime or transport result"}:::choice
  F -- "Success" --> J["Validate the result<br/>Return JSON or a report"]:::evidence
  F -- "Python error" --> H["Remove temporary hooks<br/>Explain the error"]:::limit
  W -- "Timeout / cancellation / excess output" --> K["Terminate worker group<br/>Fallback to owned child if needed"]:::limit
  K --> R["Bounded reaping + close pipes<br/>Worker memory is discarded"]:::local
  H --> S["Saved weight files stay unchanged"]:::evidence
  R --> S
  classDef input fill:#DBEAFE,stroke:#2563EB,color:#172554,stroke-width:2px;
  classDef local fill:#CCFBF1,stroke:#0F766E,color:#134E4A,stroke-width:2px;
  classDef choice fill:#FEF3C7,stroke:#B45309,color:#78350F,stroke-width:2px;
  classDef evidence fill:#EDE9FE,stroke:#7C3AED,color:#3B0764,stroke-width:2px;
  classDef remote fill:#FCE7F3,stroke:#BE185D,color:#831843,stroke-width:2px;
  classDef limit fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D,stroke-width:2px;
```

**Key point:** The checks cover implemented paths; they are not a guarantee that every
model works.

<details>
<summary>Details: bounds, failures and process cleanup</summary>

The CLI starts a total request deadline, validates options, and only then reads
explicit stdin where needed. Live work runs in a child with bounded stdout/stderr.
Both pipes are drained to avoid deadlock; strict JSON rejects malformed or
non-finite payloads. Timeouts and output overflow trigger cleanup rather than
an automatic provider switch or retry.

The worker's process group is terminated on forced cleanup. On macOS group
permission races, cleanup falls back to the owned child, bounds reaping to one
second, and closes pipes. An unkillable process produces a cleanup error. A normal
Python model error also removes installed hooks before the worker exits.

Generation defaults are copied and recorded, with one beam and one returned
sequence enforced. Invalid target ranges, zero/non-finite projection norms,
non-finite injected outputs and dtype overflow fail explicitly. These checks
bound the implemented paths; they are not proof of every model's correctness.

</details>

## 16. Glossary and a worked experiment

### Text and conversation

| Concept | ELI5 meaning |
| --- | --- |
| **Token** | A small numbered piece of text. The model works with these numbers. |
| **BOS / EOS** | Special start/end markers. An end marker at the token limit may have been forced. |
| **Template / role** | The conversation format and labels, such as user or assistant. |
| **Attention mask** | A marker telling the model which input positions to pay attention to. |
| **Token type IDs** | Optional segment labels used by some models. |
| **Context window** | How much input and output token space the model can handle. |

### Inside the model

| Concept | ELI5 meaning |
| --- | --- |
| **Weights** | Saved settings learned during training. Steering does not rewrite their files. |
| **Activation** | A value produced for the current question, like an indicator reading. |
| **MLP** | A feed-forward part of a model layer that transforms the current representation. |
| **Residual stream** | The model’s running working notes as a question moves through its layers. |
| **Projection vector** | A direction for adding one coordinate’s contribution to those working notes. |
| **Vector norm** | The length, or magnitude, of a vector. |
| **Hook** | A temporary callback attached to a part of the model. |
| **dtype** | The number format, such as float32 or bfloat16; it affects precision and rounding. |
| **Inference / eval** | Run the model without training; disable gradient tracking and training behavior such as dropout. |
| **KV cache** | Stored attention states that can save repeated work during generation; probing disables caching. |

### Running an experiment

| Concept | ELI5 meaning |
| --- | --- |
| **Calibration** | Measure a reference signal level before deciding the size of a nudge. |
| **Dose** | The signed strength of the added signal, relative to that reference level. |
| **Seed** | The starting setup for the random generator, like setting up the same dice. |
| **Temperature** | A generation setting: zero chooses non-sampling generation; positive values enable sampling. |
| **Arm** | One condition in a comparison, such as baseline, steered, or random control. |
| **Trial** | One question at one seed and dose scale, including all requested arms. |
| **SAE feature** | A coordinate in a specific sparse autoencoder. Its address is not a native MLP neuron address. |

### Reading and saving evidence

| Concept | ELI5 meaning |
| --- | --- |
| **Prompt cluster** | All trials sharing the same original question, kept together when estimating uncertainty. |
| **Bootstrap** | Repeatedly resample question groups to estimate how much a measured result might vary. |
| **Manifest** | The recipe and receipt for an experiment: settings, identities and actual inputs. |
| **Checkpoint** | A save point containing completed trials. It saves experiment progress, not trained weights. |

### A worked experiment

Suppose you want to explore arithmetic prompts versus greetings:

1. Run `inspect` to read config, then `inspect --load` to verify a supported layer.
2. Run `probe` on `examples/contrast-prompts.json` with `--positive arithmetic`
   and `--negative greeting`. Save the JSON and inspect the category counts.
3. Select a returned coordinate and an exploratory dose. Inspect both baseline
   and steered text with `generate`; the ranking does not choose a useful dose.
4. Prepare separate validation prompts with reliable answer keys. Run `evaluate`
   with repeated sampling seeds, dose scales and zero/random/reversed/ablation
   controls. Preview the generation count with `--dry-run` first.
5. Check exclusions and complete-pair denominators, compare control effects,
   inspect uncertainty and read the actual responses. Treat findings as specific
   to the recorded experiment until independently validated.
6. Preserve JSON, Markdown and manifests. Use compatible `--resume` after an
   interruption; choose fresh paths when identity changes.

The runnable discovery and evaluation commands are in the
[README examples](README.md#examples). The exact tensor math and ownership rules
are in [design.md](design.md); API-specific examples live in
[NEURONPEDIA.md](NEURONPEDIA.md).

Diagrams use editable Mermaid flowcharts and sequence diagrams with explicit
colors and accessible descriptions. Syntax references:
[flowcharts](https://mermaid.js.org/syntax/flowchart.html) and
[sequence diagrams](https://mermaid.js.org/syntax/sequenceDiagram.html).
