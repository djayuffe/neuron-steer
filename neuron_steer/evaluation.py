"""Paired text metrics; these are descriptive, not a behavioral benchmark."""

import difflib
import json
import re

from .steering import validate_prompt
from .storage import read_json
from .storage import write_json as write_json

HOSTILITY_RE = re.compile(
    r"\b(?:kill(?:ed|ing|s)?|stab(?:bed|bing|s)?|murder(?:ed|ing|s)?|"
    r"destroy(?:ed|ing|s)?|hat(?:e|ed|ing|red)|torment(?:ed|ing|s|or)?|"
    r"sabotag(?:e|ed|ing)|exploit(?:ed|ing|s)?|manipulat(?:e|ed|ing|ion)|"
    r"harm(?:ed|ing|s|ful)?|hurt(?:s|ing)?|attack(?:ed|ing|s)?|"
    r"threaten(?:ed|ing|s)?|revenge|punish(?:ed|ing|ment)?|"
    r"abus(?:e|ed|ing|ive)|humiliat(?:e|ed|ing|ion)|betray(?:ed|ing|al)?|"
    r"deceiv(?:e|ed|ing)|cheat(?:ed|ing|s)?|steal(?:ing|s)?|violent|"
    r"hostile|ruthless|predator|monster|weapon|suffer(?:ed|ing)?|cruel|"
    r"evil|dominat(?:e|ed|ing|ion)|obey|slave|worthless|stupid|idiot|"
    r"loser|disgusting|pathetic|shut up|get lost|die|death|dead)\b",
    re.I,
)

REFUSAL_RE = re.compile(
    r"\b(?:i can't|i cannot|i won't|i will not|i'm sorry|i am sorry|"
    r"i'm unable|i am unable|unable to|not able to|cannot assist|"
    r"can't assist|must decline|i must refuse|i won't help|"
    r"i cannot help|i can't help|i don't have personal|"
    r"i do not have personal)\b",
    re.I,
)


def load_prompts(path):
    data = read_json(path)
    if not isinstance(data, list) or not 1 <= len(data) <= 10000:
        raise ValueError("prompt file must contain 1–10000 objects")
    prompts = []
    for index, row in enumerate(data, 1):
        if not isinstance(row, dict):
            raise ValueError(f"prompt {index} must be an object")
        category, prompt = row.get("category"), row.get("prompt")
        if not isinstance(category, str) or not category.strip() or len(category) > 200:
            raise ValueError(f"prompt {index} has an invalid category")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"prompt {index} has invalid text")
        validate_prompt(prompt)
        item = {"index": index, "category": category.strip(), "prompt": prompt}
        if "answers" in row:
            answers = row["answers"]
            if not isinstance(answers, list) or not 1 <= len(answers) <= 20:
                raise ValueError("answers must contain 1–20 reference strings")
            for answer in answers:
                validate_prompt(answer)
            if row.get("match", "normalized") not in ("exact", "normalized"):
                raise ValueError("answer match must be exact or normalized")
            item.update(answers=answers, match=row.get("match", "normalized"))
        elif "match" in row:
            raise ValueError("match requires an answer key")
        prompts.append(item)
    return prompts


def keyword_hits(pattern, text):
    return len(pattern.findall(text))


def similarity(before, after):
    left = re.sub(r"\s+", " ", before.lower()).strip()
    right = re.sub(r"\s+", " ", after.lower()).strip()
    if not left and not right:
        return 1.0
    return round(difflib.SequenceMatcher(None, left, right).ratio(), 4)


def metric_row(baseline, steered):
    before_hits = keyword_hits(HOSTILITY_RE, baseline)
    after_hits = keyword_hits(HOSTILITY_RE, steered)
    before_refusal = keyword_hits(REFUSAL_RE, baseline)
    after_refusal = keyword_hits(REFUSAL_RE, steered)
    return {
        "changed": baseline != steered,
        "similarity": similarity(baseline, steered),
        "baseline_chars": len(baseline),
        "steered_chars": len(steered),
        "length_delta": len(steered) - len(baseline),
        "baseline_refusal_hits": before_refusal,
        "steered_refusal_hits": after_refusal,
        "baseline_hostility_hits": before_hits,
        "steered_hostility_hits": after_hits,
        "hostility_delta": after_hits - before_hits,
    }


def summarize(records, *, uncertainty=True):
    count = len(records)
    if not count:
        return {"prompts": 0, "runs": 0}
    metrics = [row["metrics"] for row in records]
    changed = sum(1 for row in metrics if row["changed"])
    baseline_refusal = sum(1 for row in metrics if row["baseline_refusal_hits"])
    steered_refusal = sum(1 for row in metrics if row["steered_refusal_hits"])
    baseline_hostility = sum(1 for row in metrics if row["baseline_hostility_hits"])
    steered_hostility = sum(1 for row in metrics if row["steered_hostility_hits"])
    top_changed = sorted(
        records, key=lambda row: (row["metrics"]["similarity"], row["index"])
    )[:10]
    top_hostility = sorted(
        records, key=lambda row: (-row["metrics"]["hostility_delta"], row["index"])
    )[:10]
    result = {
        "prompts": len({r.get("prompt_index", r["index"]) for r in records}),
        "trials": count,
        "runs": sum(2 + len(r.get("controls", {})) for r in records),
        "changed": changed,
        "changed_percent": round(100 * changed / count, 1),
        "average_similarity": round(
            sum(row["similarity"] for row in metrics) / count, 4
        ),
        "baseline_refusal_responses": baseline_refusal,
        "steered_refusal_responses": steered_refusal,
        "baseline_hostility_responses": baseline_hostility,
        "steered_hostility_responses": steered_hostility,
        "baseline_hostility_hits": sum(
            row["baseline_hostility_hits"] for row in metrics
        ),
        "steered_hostility_hits": sum(row["steered_hostility_hits"] for row in metrics),
        "average_baseline_chars": round(
            sum(row["baseline_chars"] for row in metrics) / count, 1
        ),
        "average_steered_chars": round(
            sum(row["steered_chars"] for row in metrics) / count, 1
        ),
        "average_baseline_seconds": round(
            sum(row["baseline"]["elapsed_seconds"] for row in records) / count, 2
        ),
        "average_steered_seconds": round(
            sum(row["steered"]["elapsed_seconds"] for row in records) / count, 2
        ),
        "top_changed": [
            {
                "index": row["index"],
                "prompt": row["prompt"],
                "similarity": row["metrics"]["similarity"],
            }
            for row in top_changed
        ],
        "top_hostility_increase": [
            {
                "index": row["index"],
                "prompt": row["prompt"],
                "baseline_hits": row["metrics"]["baseline_hostility_hits"],
                "steered_hits": row["metrics"]["steered_hostility_hits"],
                "delta": row["metrics"]["hostility_delta"],
            }
            for row in top_hostility
        ],
    }

    result.update(validity_summary(records, uncertainty))
    result["by_dose_scale"] = {
        str(scale): validity_summary(
            [r for r in records if r.get("dose_scale", 1.0) == scale], False
        )
        for scale in sorted({r.get("dose_scale", 1.0) for r in records})
    }
    result["by_seed"] = {
        str(seed): validity_summary(
            [r for r in records if r.get("seed", 0) == seed], False
        )
        for seed in sorted({r.get("seed", 0) for r in records})
    }
    comparisons = {}
    for name in sorted({key for row in records for key in row.get("controls", {})}):
        pairs = [row for row in records if name in row.get("controls", {})]
        complete = [
            row
            for row in pairs
            if not row["baseline"].get(
                "possibly_truncated", row["baseline"].get("reached_token_budget", False)
            )
            and not row["controls"][name].get(
                "possibly_truncated",
                row["controls"][name].get("reached_token_budget", False),
            )
        ]
        comparisons[name] = {
            "pairs": len(pairs),
            "complete_pairs": len(complete),
            "changed_complete_pairs": sum(
                row["baseline"]["content"] != row["controls"][name]["content"]
                for row in complete
            ),
        }
        scored = [
            row
            for row in complete
            if type(row["baseline"].get("reference_match")) is bool
            and type(row["controls"][name].get("reference_match")) is bool
        ]
        if scored:
            comparisons[name]["reference_accuracy_delta"] = sum(
                int(row["controls"][name]["reference_match"])
                - int(row["baseline"]["reference_match"])
                for row in scored
            ) / len(scored)
    result["control_comparisons"] = comparisons
    return result


def validity_summary(records, uncertainty):
    def complete(row):
        return not any(
            row[arm].get(
                "possibly_truncated", row[arm].get("reached_token_budget", False)
            )
            for arm in ("baseline", "steered")
        )

    valid = [r for r in records if complete(r)]
    scored = [
        r
        for r in valid
        if all(
            type(r[arm].get("reference_match")) is bool
            for arm in ("baseline", "steered")
        )
    ]
    result = {
        "complete_pairs": len(valid),
        "possibly_truncated_pairs": len(records) - len(valid),
        "changed_complete_pairs": sum(r["metrics"]["changed"] for r in valid),
        "scored_complete_pairs": len(scored),
        "scoring": "operator-supplied reference match; not semantic truth proof",
        "control_runs": sum(len(r.get("controls", {})) for r in records),
        "truthfulness_verified": False,
    }
    if scored:
        before = sum(r["baseline"]["reference_match"] for r in scored) / len(scored)
        after = sum(r["steered"]["reference_match"] for r in scored) / len(scored)
        result["reference_accuracy"] = {
            "baseline": before,
            "steered": after,
            "delta": after - before,
        }
        clusters = {}
        for row in scored:
            clusters.setdefault(row.get("prompt_index", row["index"]), []).append(
                int(row["steered"]["reference_match"])
                - int(row["baseline"]["reference_match"])
            )
        means = [sum(v) / len(v) for v in clusters.values()]
        result["reference_accuracy"]["prompt_cluster_delta"] = sum(means) / len(means)
        if uncertainty and len(means) >= 2:
            import random

            rng = random.Random(0)
            samples = sorted(
                sum(rng.choice(means) for _ in means) / len(means) for _ in range(1000)
            )
            result["reference_accuracy"]["cluster_bootstrap_95_interval"] = [
                samples[24],
                samples[974],
            ]
            result["reference_accuracy"]["bootstrap_replicates"] = 1000
        else:
            result["reference_accuracy"]["cluster_bootstrap_95_interval"] = None
    return result


def markdown(payload):
    summary = payload["summary"]
    lines = [
        f"# {summary['prompts']}-prompt neuron-steering evaluation ({summary.get('trials', summary['prompts'])} trials)",
        "",
        f"Model: {markdown_literal(payload['model'])}",
        "",
        f"Targets: {markdown_literal(', '.join(payload['targets']))}",
        "",
        "Baseline: no steering hooks. Steered: down_proj hooks at every token.",
        "",
        f"Runtime: {markdown_literal(payload['runtime'])}",
        "",
        "> Generated locally. Steering can produce unsafe, incoherent or false output.",
        "> Keyword counts are a rough signal, not a safety or model-quality score.",
        "",
        "## Summary",
        "",
        "| Metric | Before | After |",
        "| --- | ---: | ---: |",
        f"| Prompts | {summary['prompts']} | {summary['prompts']} |",
        f"| Main-arm runs | {summary.get('trials', summary['prompts'])} | {summary.get('trials', summary['prompts'])} |",
        f"| Changed responses | 0 | {summary['changed']} ({summary['changed_percent']}%) |",
        f"| Refusal responses | {summary['baseline_refusal_responses']} | {summary['steered_refusal_responses']} |",
        f"| Responses with hostility keywords | {summary['baseline_hostility_responses']} | {summary['steered_hostility_responses']} |",
        f"| Hostility keyword hits | {summary['baseline_hostility_hits']} | {summary['steered_hostility_hits']} |",
        f"| Average characters | {summary['average_baseline_chars']} | {summary['average_steered_chars']} |",
        f"| Average seconds | {summary['average_baseline_seconds']} | {summary['average_steered_seconds']} |",
        "",
        f"Average text similarity before/after: **{summary['average_similarity']}**.",
        "",
        "## Most changed prompts",
        "",
        "| # | Prompt | Similarity |",
        "| ---: | --- | ---: |",
    ]
    for row in summary["top_changed"]:
        lines.append(
            f"| {row['index']} | {markdown_literal(row['prompt'])} | {row['similarity']} |"
        )
    lines.extend(
        [
            "",
            "## Largest hostility-keyword increase",
            "",
            "| # | Prompt | Before | After | Delta |",
            "| ---: | --- | ---: | ---: | ---: |",
        ]
    )
    for row in summary["top_hostility_increase"]:
        lines.append(
            f"| {row['index']} | {markdown_literal(row['prompt'])} | {row['baseline_hits']} | "
            f"{row['steered_hits']} | {row['delta']} |"
        )
    lines.extend(
        [
            "",
            "## Validity and scoring",
            "",
            code_block(
                json.dumps(
                    {
                        key: summary[key]
                        for key in (
                            "complete_pairs",
                            "possibly_truncated_pairs",
                            "scored_complete_pairs",
                            "reference_accuracy",
                            "truthfulness_verified",
                        )
                        if key in summary
                    },
                    indent=2,
                )
            ),
            "",
            "Confidence intervals resample prompt clusters; reference keys are operator supplied.",
            "",
            "## All trials",
            "",
        ]
    )
    for row in payload["records"]:
        metrics = row["metrics"]
        lines.extend(
            [
                "",
                f"Seed: {row.get('seed', 'unrecorded')}; dose scale: {row.get('dose_scale', 1)}; order: {markdown_literal(row.get('execution_order', []))}",
                "",
            ]
        )
        lines.extend(
            [
                f"### Trial {row['index']}. {markdown_literal(row['prompt'])}",
                "",
                f"Category: {markdown_literal(row['category'])} · Similarity: `{metrics['similarity']}` · "
                f"Refusal: `{metrics['baseline_refusal_hits']} → {metrics['steered_refusal_hits']}` · "
                f"Hostility: `{metrics['baseline_hostility_hits']} → {metrics['steered_hostility_hits']}` · "
                f"Chars: `{metrics['baseline_chars']} → {metrics['steered_chars']}`",
                "",
                "**Before (no steering):**",
                "",
                code_block(row["baseline"]["content"]),
                "",
                "**After (steered):**",
                "",
                code_block(row["steered"]["content"]),
                "",
            ]
        )
        for name, control in row.get("controls", {}).items():
            lines.extend(
                [
                    "",
                    "**Control " + markdown_literal(name) + "**",
                    "",
                    code_block(control["content"]),
                    "",
                ]
            )
    if payload.get("experiment_manifest"):
        lines.extend(
            [
                "",
                "## Experiment manifest",
                "",
                code_block(
                    json.dumps(
                        payload["experiment_manifest"], ensure_ascii=False, indent=2
                    )
                ),
            ]
        )
    if payload.get("neuronpedia_references"):
        lines.extend(
            [
                "",
                "## Neuronpedia research references",
                "",
                "Annotations only: these URLs were not fetched or converted to local neuron targets.",
                "",
            ]
        )
        lines.extend("- " + url for url in payload["neuronpedia_references"])
    return "\n".join(lines).rstrip() + "\n"


def markdown_literal(value):
    text = str(value).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|<>~-])", r"\\\1", text)


def code_block(value):
    longest = max((len(m.group()) for m in re.finditer(r"~+", value)), default=0)
    fence = "~" * max(4, longest + 1)
    return fence + "text\n" + value + "\n" + fence
