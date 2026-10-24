"""Manifest signing with cosign (KMS-backed key, e.g. awskms:///alias/imagefactory-signing).

The runner is injectable so promotion logic can be tested without cosign installed.
"""
from __future__ import annotations

import base64
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, List

from .manifest import Manifest

Runner = Callable[[List[str]], None]


def _run(cmd: List[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise SigningError("cosign not found on PATH")
    except subprocess.CalledProcessError as e:
        raise SigningError("%s failed: %s" % (cmd[1], e.stderr.strip()))


class SigningError(Exception):
    pass


def sign(m: Manifest, key_ref: str, run: Runner = None) -> str:
    run = run or _run
    with tempfile.TemporaryDirectory() as d:
        payload, sig = Path(d, "manifest.bin"), Path(d, "manifest.sig")
        payload.write_bytes(m.canonical_bytes())
        # Do NOT upload to the public Sigstore transparency log: by default cosign publishes the signature and public key
        # of every signature, which a private pipeline must not do silently, and it would make every verification
        # depend on that third-party service. Trust here rests on the KMS key, not on a public log.
        run(["cosign", "sign-blob", "--yes", "--tlog-upload=false", "--key", key_ref,
             "--output-signature", str(sig), str(payload)])
        if not sig.exists() or not sig.stat().st_size:
            raise SigningError("cosign produced no signature for %s" % m.version)
        return base64.b64encode(sig.read_bytes()).decode()


def verify(m: Manifest, key_ref: str, run: Runner = None) -> None:
    run = run or _run
    if not m.signature:
        raise SigningError("manifest %s is not signed" % m.version)
    with tempfile.TemporaryDirectory() as d:
        payload, sig = Path(d, "manifest.bin"), Path(d, "manifest.sig")
        payload.write_bytes(m.canonical_bytes())
        sig.write_bytes(base64.b64decode(m.signature))
        # Matches --tlog-upload=false above: there is no log entry to look up, so do not query the public log.
        run(["cosign", "verify-blob", "--insecure-ignore-tlog=true", "--key", key_ref,
             "--signature", str(sig), str(payload)])
