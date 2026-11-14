"""Strict bounded JSON and atomic report writes."""

import json
import os
import pathlib
import tempfile

from .steering import NeuronSteerError


def strict_json(source, max_bytes=8 * 1024 * 1024):
    if len(source.encode("utf-8")) > max_bytes:
        raise NeuronSteerError("JSON exceeds its byte limit")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise NeuronSteerError("JSON contains duplicate keys")
            result[key] = value
        return result

    def constant(value):
        raise NeuronSteerError("JSON contains a non-finite number")

    try:
        result = json.loads(source, object_pairs_hook=pairs, parse_constant=constant)
        pending = [(result, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 32:
                raise NeuronSteerError("JSON exceeds the nesting limit")
            if isinstance(item, dict):
                pending.extend((v, depth + 1) for v in item.values())
            elif isinstance(item, list):
                pending.extend((v, depth + 1) for v in item)
        return result
    except (ValueError, RecursionError) as exc:
        raise NeuronSteerError("invalid JSON") from exc


def read_json(path, max_bytes=8 * 1024 * 1024):
    with pathlib.Path(path).open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise NeuronSteerError("JSON file exceeds its byte limit")
    return strict_json(data.decode("utf-8"), max_bytes)


def atomic_text(path, text):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix="." + path.name + ".",
            delete=False,
        ) as stream:
            name = stream.name
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def write_json(path, payload):
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if len(text.encode("utf-8")) > 128 * 1024 * 1024:
        raise NeuronSteerError("checkpoint exceeds the 128 MiB limit")
    atomic_text(path, text)
