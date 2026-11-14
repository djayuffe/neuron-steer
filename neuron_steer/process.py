"""Bounded child pipes and process-group cleanup on macOS/Linux."""

import os
import selectors
import signal
import subprocess
import time

from .steering import NeuronSteerError


def bounded_run(cmd, *, input=None, timeout=30, max_bytes=8 * 1024 * 1024, **kwargs):
    """Drain both pipes concurrently with bounded memory and one request deadline."""
    budget = timeout
    expires = time.monotonic() + budget
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    pending = memoryview((input or "").encode("utf-8"))
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=kwargs.get("env"),
    )
    try:
        with selectors.DefaultSelector() as selector:
            for name in buffers:
                stream = getattr(process, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            if process.stdin:
                os.set_blocking(process.stdin.fileno(), False)
                if pending:
                    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                else:
                    process.stdin.close()
            while selector.get_map():
                remaining = expires - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(cmd, budget)
                for key, _ in selector.select(min(remaining, 0.25)):
                    if key.data == "stdin":
                        try:
                            written = os.write(key.fd, pending[:8192])
                            pending = pending[written:]
                        except BrokenPipeError:
                            pending = pending[:0]
                        if not pending:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        if len(buffers[key.data]) + len(chunk) > max_bytes:
                            raise NeuronSteerError(
                                f"Subprocess {key.data} exceeded the {max_bytes}-byte limit"
                            )
                        buffers[key.data].extend(chunk)
            process.wait(timeout=max(0.001, expires - time.monotonic()))
        return subprocess.CompletedProcess(
            cmd,
            process.returncode,
            buffers["stdout"].decode("utf-8", "replace"),
            buffers["stderr"].decode("utf-8", "replace"),
        )
    finally:
        # Also clean up descendants that inherited a pipe on timeout/cancellation.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream and not stream.closed:
                stream.close()
