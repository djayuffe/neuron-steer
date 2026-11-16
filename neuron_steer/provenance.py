"""Recoverable experiment identity, including prompt policy and executable source."""

import hashlib
import importlib.metadata
import json
import pathlib
import platform
import sys

from . import __version__
from .steering import NeuronSteerError


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def hash_file(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def source_identity():
    root = pathlib.Path(__file__).parent
    return {file.name: hash_file(file) for file in sorted(root.glob("*.py"))}


def manifest(job, prompts=None):
    from .runtime import SYSTEM_MESSAGE

    model = pathlib.Path(job["model"]).expanduser().resolve()
    names = {
        "config.json",
        "generation_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "added_tokens.json",
        "vocab.json",
        "vocab.txt",
        "merges.txt",
        "tokenizer.model",
        "spiece.model",
        "sentencepiece.bpe.model",
    }
    assets = []
    paths = {
        p
        for p in model.rglob("*")
        if p.is_file()
        and not any(s.startswith(".") for s in p.relative_to(model).parts)
        and (
            p.name in names
            or p.suffix in (".safetensors", ".tiktoken", ".jinja")
            or p.name.endswith(".safetensors.index.json")
        )
    }
    for index in tuple(paths):
        if index.name.endswith(".safetensors.index.json"):
            from .storage import read_json

            data = read_json(index)
            if not isinstance(data, dict) or not isinstance(
                data.get("weight_map"), dict
            ):
                raise NeuronSteerError("invalid safetensors index")
            for value in data["weight_map"].values():
                if not isinstance(value, str):
                    raise NeuronSteerError("invalid shard path")
                relative = pathlib.PurePosixPath(value)
                if relative.is_absolute() or ".." in relative.parts:
                    raise NeuronSteerError(
                        "shard paths must stay within the selected checkpoint"
                    )
                shard = model / value
                if not shard.is_file():
                    raise NeuronSteerError("missing local safetensors shard: " + value)
                paths.add(shard)
    for path in sorted(paths):
        stat = path.stat()
        entry = {
            "path": path.relative_to(model).as_posix(),
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
        if job.get("asset_identity", "metadata") == "sha256":
            entry["sha256"] = hash_file(path)
        assets.append(entry)
    versions = {}
    for name in ("torch", "transformers", "tokenizers", "safetensors", "jinja2"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    defaults = {
        "system_role": "auto",
        "chat_template": None,
        "calibration": "skip-first",
        "asset_identity": "metadata",
        "seeds": [job.get("seed", 0)],
        "controls": [],
        "dose_scales": [1.0],
        "order": "alternate",
    }
    settings = {
        key: job[key]
        for key in (
            "max_tokens",
            "temperature",
            "context_size",
            "seed",
            "no_thinking",
            "device",
        )
    }
    settings.update({key: job.get(key, value) for key, value in defaults.items()})
    return {
        "format_version": 3,
        "package_version": __version__,
        "operation": job["operation"],
        "model": str(model),
        "targets": job["neurons"],
        "baseline": job.get("baseline", False),
        "settings": settings,
        "system_message": SYSTEM_MESSAGE,
        "prompts": prompts,
        "references": job.get("references", []),
        "source_sha256": source_identity(),
        "model_assets": assets,
        "identity_strength": job.get("asset_identity", "metadata"),
        "runtime_packages": versions,
        "python": sys.version.split()[0],
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "release": platform.release(),
        },
        "report_paths": {key: job.get(key) for key in ("output", "markdown_out")},
    }
