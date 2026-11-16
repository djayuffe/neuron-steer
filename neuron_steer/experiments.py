"""Bounded repeated trials and explicit intervention controls."""

import math

from .steering import MAX_DOSE, NeuronSteerError


def normalize_experiment(job, specs):
    seeds = job.get("seeds")
    if seeds is None:
        seeds = [job.get("seed", 0)]
    if isinstance(seeds, str):
        try:
            seeds = [int(value) for value in seeds.split(",")]
        except ValueError as exc:
            raise NeuronSteerError("--seeds must be comma-separated integers") from exc
    if (
        not isinstance(seeds, list)
        or not 1 <= len(seeds) <= 32
        or any(type(v) is not int or not -100000000 <= v <= 100000000 for v in seeds)
        or len(set(seeds)) != len(seeds)
    ):
        raise NeuronSteerError("use 1–32 unique integer seeds within ±100000000")
    scales = job.get("dose_scales", [1.0])
    if isinstance(scales, str):
        try:
            scales = [float(value) for value in scales.split(",")]
        except ValueError as exc:
            raise NeuronSteerError(
                "--dose-scales must be comma-separated finite numbers"
            ) from exc
    if (
        not isinstance(scales, list)
        or not 1 <= len(scales) <= 21
        or any(type(v) not in (float, int) or not math.isfinite(v) for v in scales)
        or len(set(scales)) != len(scales)
    ):
        raise NeuronSteerError("use 1–21 unique finite dose scales")
    if any(abs(spec["dose"] * scale) > MAX_DOSE for spec in specs for scale in scales):
        raise NeuronSteerError("scaled doses exceed the ±10 target bound")
    controls = job.get("controls", [])
    if (
        not isinstance(controls, list)
        or any(c not in ("zero", "random", "reversed", "ablation") for c in controls)
        or len(set(controls)) != len(controls)
    ):
        raise NeuronSteerError(
            "controls must be unique zero, random, reversed or ablation values"
        )
    if job.get("order", "alternate") not in ("baseline-first", "alternate"):
        raise NeuronSteerError("unknown experiment order")
    job.update(
        seeds=seeds,
        dose_scales=[float(v) for v in scales],
        controls=controls,
        order=job.get("order", "alternate"),
    )


def trials(prompts, job):
    count = (
        len(prompts)
        * len(job.get("seeds", [job["seed"]]))
        * len(job.get("dose_scales", [1.0]))
    )
    if count > 10000:
        raise NeuronSteerError(
            "experiment exceeds 10000 paired trials; reduce prompts/seeds/doses"
        )
    rows = []
    for row in prompts:
        for seed in job.get("seeds", [job["seed"]]):
            for scale in job.get("dose_scales", [1.0]):
                rows.append(
                    {
                        **row,
                        "prompt_index": row["index"],
                        "index": len(rows) + 1,
                        "seed": seed,
                        "dose_scale": scale,
                    }
                )
    return rows


def conditions(specs, scale, controls):
    selected = [{**spec, "dose": spec["dose"] * scale} for spec in specs]
    result = {"baseline": ([], "target"), "steered": (selected, "target")}
    for control in controls:
        if control == "zero":
            result["zero"] = ([{**s, "dose": 0.0} for s in selected], "target")
        elif control == "random":
            result["random"] = (selected, "random")
        elif control == "reversed":
            result["reversed"] = (
                [{**s, "dose": -s["dose"]} for s in selected],
                "target",
            )
        elif control == "ablation":
            for index, spec in enumerate(selected):
                result[f"without_{spec['layer']}_{spec['neuron']}"] = (
                    [s for i, s in enumerate(selected) if i != index],
                    "target",
                )
    return result


def match_answer(content, row):
    answers = row.get("answers")
    if answers is None:
        return None
    if row.get("match", "normalized") == "exact":
        return content in answers

    def normalized(text):
        return " ".join(text.split()).casefold()

    return normalized(content) in [normalized(answer) for answer in answers]
