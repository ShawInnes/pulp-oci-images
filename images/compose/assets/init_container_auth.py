#!/usr/bin/env python3
"""Initialize the persistent ES256 key pair used by container registry tokens."""

import argparse
import fcntl
import os
from pathlib import Path
import sys
import tempfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _write_atomic(path, data, mode):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as out:
            temporary = Path(out.name)
            os.fchmod(out.fileno(), mode)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def initialize(cert_dir):
    cert_dir = Path(cert_dir)
    cert_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    private_path = cert_dir / "token_private_key.pem"
    public_path = cert_dir / "token_public_key.pem"
    lock_path = cert_dir / ".container-auth.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(lock_path, flags, 0o600), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for path in (private_path, public_path):
            if path.is_symlink():
                raise RuntimeError(f"Refusing a symlink at {path}")
        if public_path.exists() and not private_path.exists():
            raise RuntimeError("Public token key exists without its private key; restore the matching private key")
        if private_path.exists():
            private = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
            if not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(private.curve, ec.SECP256R1):
                raise RuntimeError("Existing private token key must be an ES256/P-256 EC key")
            result = "Reused existing container token key pair"
        else:
            private = ec.generate_private_key(ec.SECP256R1())
            _write_atomic(private_path, private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ), 0o600)
            result = "Created container token key pair"
        public = private.public_key()
        if public_path.exists():
            existing = serialization.load_pem_public_key(public_path.read_bytes())
            if not isinstance(existing, ec.EllipticCurvePublicKey) or existing.public_numbers() != public.public_numbers():
                raise RuntimeError("Container token public/private keys do not match; restore the matching pair")
        else:
            _write_atomic(public_path, public.public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ), 0o644)
            if result.startswith("Reused"):
                result = "Recovered public container token key from existing private key"
        private_path.chmod(0o600)
        public_path.chmod(0o644)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert-dir", type=Path, default=Path("/etc/pulp/certs"))
    args = parser.parse_args()
    try:
        print(initialize(args.cert_dir))
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        print(f"Container token key initialization failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
