"""Explicit Neuronpedia requests, isolated from the offline local model runtime."""

import http.client
import ipaddress
import json
import math
import os
import re
import ssl
import time
import urllib.parse

from .steering import NeuronSteerError, validate_prompt
from .storage import strict_json

ORIGIN = "https://www.neuronpedia.org"
OPERATIONS = (
    "models",
    "model",
    "source",
    "feature",
    "search",
    "activate",
    "top",
    "steer",
    "link",
)
TEXT_OPERATIONS = ("search", "activate", "top", "steer")
MAX_RESPONSE = 8 * 1024 * 1024


def identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", value
    ):
        raise NeuronSteerError(
            label
            + " must be an exact Neuronpedia ID (letters, digits, dots, underscores or hyphens)"
        )
    return value


def feature_url(model, source, index):
    identifier(model, "model")
    identifier(source, "source")
    if type(index) is not int or not 0 <= index <= 2147483647:
        raise NeuronSteerError("feature index must be 0–2147483647")
    return f"{ORIGIN}/{model}/{source}/{index}"


def parse_feature_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc not in ("www.neuronpedia.org", "neuronpedia.org")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid origin")
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 3 or not re.fullmatch(r"\d{1,10}", parts[2]):
            raise ValueError("expected model/source/index")
        model, source, index = parts[0], parts[1], int(parts[2])
        return {
            "model": model,
            "source": source,
            "index": index,
            "url": feature_url(model, source, index),
        }
    except (ValueError, TypeError, AttributeError) as exc:
        raise NeuronSteerError(
            "expected an official HTTPS Neuronpedia feature URL without query or fragment"
        ) from exc


def normalize_references(values):
    if not isinstance(values, list) or len(values) > 16:
        raise NeuronSteerError("at most 16 Neuronpedia reference URLs are supported")
    return list(dict.fromkeys(parse_feature_url(value)["url"] for value in values))


def endpoint_parts(endpoint):
    if not isinstance(endpoint, str) or any(
        ord(c) <= 32 or ord(c) == 127 for c in endpoint
    ):
        raise NeuronSteerError("invalid Neuronpedia endpoint")
    if endpoint.rstrip("/") in (ORIGIN, "https://neuronpedia.org"):
        host = urllib.parse.urlsplit(endpoint).hostname
        return "https", host, host, 443, "", True
    try:
        parsed = urllib.parse.urlsplit(endpoint)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid URL")
        host = parsed.hostname.rstrip(".").lower()
        address = (
            "127.0.0.1" if host == "localhost" else str(ipaddress.ip_address(host))
        )
        ip = ipaddress.ip_address(address)
        if not ip.is_loopback and not (
            getattr(ip, "ipv4_mapped", None) and ip.ipv4_mapped.is_loopback
        ):
            raise ValueError("not loopback")
        port = (
            parsed.port
            if parsed.port is not None
            else 443
            if parsed.scheme == "https"
            else 80
        )
        if not 1 <= port <= 65535 or "%" in host:
            raise ValueError("invalid port/address")
        path = parsed.path.rstrip("/")
        if any(piece in (".", "..") for piece in urllib.parse.unquote(path).split("/")):
            raise ValueError("path traversal")
        return (
            parsed.scheme,
            host,
            address,
            port,
            urllib.parse.quote(path, safe="/%-._~"),
            False,
        )
    except (ValueError, TypeError) as exc:
        raise NeuronSteerError(
            "endpoint must be the official HTTPS Neuronpedia origin or a loopback webapp URL"
        ) from exc


class NeuronpediaClient:
    def __init__(self, endpoint=ORIGIN, *, api_key_env=None, ca_file=None):
        self.parts = endpoint_parts(endpoint)
        self.endpoint = endpoint.rstrip("/")
        if api_key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
            raise NeuronSteerError(
                "API key must be supplied by an environment variable name"
            )
        self.api_key_env, self.ca_file = api_key_env, ca_file

    def request(self, path, body=None, *, deadline):
        if not path.startswith("/api/") or any(
            c in path for c in ("?", "#", "\r", "\n")
        ):
            raise NeuronSteerError("invalid Neuronpedia API path")
        scheme, host, address, port, base, _ = self.parts

        def remaining():
            left = deadline - time.monotonic()
            if left <= 0:
                raise NeuronSteerError("Neuronpedia command deadline exceeded")
            return left

        token = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        if self.api_key_env and not token:
            raise NeuronSteerError("Neuronpedia API-key environment variable is unset")
        if any(ord(c) < 32 or ord(c) > 126 for c in token) or len(token) > 8192:
            raise NeuronSteerError("invalid Neuronpedia API credential")
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
            "Host": ("[" + host + "]" if ":" in host else host) + ":" + str(port),
        }
        if token:
            headers["x-api-key"] = token
        payload = (
            json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if body is not None
            else None
        )
        if payload is not None and len(payload) > 128 * 1024:
            raise NeuronSteerError("Neuronpedia request exceeds 128 KiB")
        connection = http.client.HTTPConnection(address, port, timeout=remaining())
        try:
            connection.connect()
            if scheme == "https":
                context = ssl.create_default_context(cafile=self.ca_file)
                connection.sock = context.wrap_socket(
                    connection.sock, server_hostname=host
                )
            transport = connection.sock
            transport.settimeout(remaining())
            connection.request(
                "POST" if payload is not None else "GET",
                base + path,
                body=payload,
                headers=headers,
            )
            transport.settimeout(remaining())
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                hint = (
                    "rate limit reached; retry later"
                    if response.status == 429
                    else "check API key and resource permissions"
                    if response.status in (401, 403)
                    else "resource not found"
                    if response.status == 404
                    else "redirects are refused"
                    if 300 <= response.status < 400
                    else "check API parameters and model/source inference availability"
                )
                raise NeuronSteerError(f"Neuronpedia HTTP {response.status}: {hint}")
            length = response.getheader("Content-Length")
            if length is not None:
                try:
                    if not 0 <= int(length) <= MAX_RESPONSE:
                        raise ValueError("outside bound")
                except ValueError as exc:
                    raise NeuronSteerError(
                        "invalid or oversized Neuronpedia Content-Length"
                    ) from exc
            if response.getheader("Content-Encoding", "identity").lower() not in (
                "identity",
                "",
            ):
                raise NeuronSteerError(
                    "compressed Neuronpedia responses are not accepted"
                )
            chunks = bytearray()
            while not response.isclosed():
                transport.settimeout(remaining())
                chunk = response.read1(min(8192, MAX_RESPONSE + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
                if len(chunks) > MAX_RESPONSE:
                    raise NeuronSteerError("Neuronpedia response exceeds 8 MiB")
            if length is not None and len(chunks) != int(length):
                raise NeuronSteerError("incomplete Neuronpedia response body")
            data = strict_json(chunks.decode("utf-8"), MAX_RESPONSE)
            if (
                not isinstance(data, (dict, list))
                or isinstance(data, dict)
                and data.get("error")
            ):
                raise NeuronSteerError(
                    "Neuronpedia returned an invalid resource or API error"
                )
            return data
        except (OSError, http.client.HTTPException, UnicodeError) as exc:
            # Never reflect credential-bearing headers or potentially stored prompt text.
            raise NeuronSteerError(
                "Neuronpedia transport failed; check connectivity, TLS and timeout"
            ) from exc
        finally:
            connection.close()


def build_request(args, text=None):
    endpoint_parts(args.endpoint)
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 300:
        raise NeuronSteerError("Neuronpedia --timeout must be 1–300 seconds")
    if args.api_key_env and not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*", args.api_key_env
    ):
        raise NeuronSteerError("--api-key-env requires an environment variable name")
    if not 0 <= args.offset <= 1000000 or not 1 <= args.limit <= 100:
        raise NeuronSteerError("--offset must be 0–1000000 and --limit 1–100")
    if (
        not math.isfinite(args.strength)
        or abs(args.strength) > 1000
        or not math.isfinite(args.temperature)
        or not 0 <= args.temperature <= 2
    ):
        raise NeuronSteerError("strength must be finite ±1000; temperature finite 0–2")
    if not 1 <= args.max_tokens <= 256 or not -100000000 <= args.seed <= 100000000:
        raise NeuronSteerError("Neuronpedia max tokens must be 1–256; seed ±100000000")
    if args.url:
        reference = parse_feature_url(args.url)
        if args.action not in ("feature", "activate", "steer", "link"):
            raise NeuronSteerError(
                "--url is only used by feature, activate, steer or link"
            )
        if any(
            getattr(args, k) is not None and getattr(args, k) != reference[k]
            for k in ("model", "source", "index")
        ):
            raise NeuronSteerError("--url conflicts with explicit resource identifiers")
        args.model, args.source, args.index = (
            reference["model"],
            reference["source"],
            reference["index"],
        )
    op = args.action
    if (
        op == "models"
        and args.model
        or args.source_set
        and op not in ("source", "top")
        or args.index is not None
        and op not in ("feature", "activate", "steer", "link")
        or args.source
        and op in ("models", "model", "source")
    ):
        raise NeuronSteerError("resource flags do not apply to this operation")
    if (op in TEXT_OPERATIONS) != args.stdin:
        raise NeuronSteerError(
            "search, activate, top and steer require --stdin; metadata/link commands do not accept it"
        )
    model = identifier(args.model, "--model") if op != "models" else None
    source = (
        identifier(args.source, "--source")
        if op in ("feature", "search", "activate", "steer", "link") or args.source
        else None
    )
    source_set = (
        identifier(args.source_set, "--source-set") if op in ("source", "top") else None
    )
    if args.index is not None and not 0 <= args.index <= 2147483647:
        raise NeuronSteerError("feature index must be 0–2147483647")
    if op in ("feature", "activate", "steer", "link") and args.index is None:
        raise NeuronSteerError(op + " requires --index or --url")
    # Validate flags before any blocking stdin read in the CLI.
    if op in TEXT_OPERATIONS and text is None:
        return None, None
    if op in TEXT_OPERATIONS:
        validate_prompt(text)
    if op == "models":
        return "/api/model/list", {}
    if op == "model":
        return "/api/model/get", {"id": model}
    if op == "source":
        return f"/api/source-set/{model}/{source_set}", None
    if op == "feature":
        return f"/api/feature/{model}/{source}/{args.index}", None
    if op == "link":
        return None, None
    if op == "search":
        if len(text.strip()) < 3:
            raise NeuronSteerError("explanation search requires at least 3 characters")
        return "/api/explanation/search", {
            "modelId": model,
            "layers": [source],
            "query": text,
            "offset": args.offset,
        }
    if op == "activate":
        return "/api/activation/new", {
            "feature": {"modelId": model, "source": source, "index": str(args.index)},
            "customText": text,
            "ignoreBos": True,
        }
    if op == "top":
        return "/api/search-all", {
            "modelId": model,
            "sourceSet": source_set,
            "text": text,
            "selectedLayers": [source] if source else [],
            "sortIndexes": [],
            "ignoreBos": True,
            "numResults": args.limit,
            "densityThreshold": -1,
        }
    if op == "steer":
        return "/api/steer", {
            "modelId": model,
            "prompt": text,
            "features": [
                {
                    "modelId": model,
                    "layer": source,
                    "index": args.index,
                    "strength": args.strength,
                }
            ],
            "temperature": args.temperature,
            "n_tokens": args.max_tokens,
            "seed": args.seed,
            "presence_penalty": 0,
            "strength_multiplier": 1,
            "steer_method": "SIMPLE_ADDITIVE",
            "stream": False,
        }
    raise NeuronSteerError("unknown Neuronpedia operation")


def run(args, deadline, read_text):
    path, body = build_request(args)
    if args.action in TEXT_OPERATIONS:
        path, body = build_request(args, read_text(deadline))
    hosted = endpoint_parts(args.endpoint)[-1]
    result = {
        "provider": "neuronpedia",
        "operation": args.action,
        "endpoint": args.endpoint.rstrip("/"),
        "hosted": hosted,
        "private_history_accessed": False,
        "model_loaded": False,
        "local_neuron_targets_applied": False,
        "semantic_truth_verified": False,
        "text_submitted": args.action in TEXT_OPERATIONS and not args.dry_run,
        "server_may_store_text": args.action in TEXT_OPERATIONS,
    }
    if args.action in ("feature", "activate", "steer", "link"):
        result["feature_url"] = (
            args.endpoint.rstrip("/")
            + "/"
            + "/".join((args.model, args.source, str(args.index)))
        )
    if args.dry_run or args.action == "link":
        result.update(
            dry_run=args.dry_run,
            network_request_performed=False,
            path=path,
            body=body,
            method="POST" if body is not None else "GET" if path else None,
        )
    else:
        client = NeuronpediaClient(
            args.endpoint, api_key_env=args.api_key_env, ca_file=args.ca_file
        )
        data = client.request(path, body, deadline=deadline)
        if args.action == "models" and not isinstance(data, list):
            raise NeuronSteerError("Neuronpedia model inventory must be a list")
        if args.action == "feature" and (
            not isinstance(data, dict)
            or data.get("modelId") != args.model
            or data.get("layer") != args.source
            or str(data.get("index")) != str(args.index)
        ):
            raise NeuronSteerError(
                "Neuronpedia feature response does not match the requested identifiers"
            )
        result.update(data=data, network_request_performed=True)
    return result
