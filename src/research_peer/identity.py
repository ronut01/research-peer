from __future__ import annotations

import base64
import hashlib
import os
import ssl
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .paths import Paths
from .protocol import ProtocolError


def _run(args: list[str], *, input_data: bytes | None = None) -> bytes:
    try:
        completed = subprocess.run(args, input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("OpenSSL is required but was not found") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"OpenSSL failed: {detail}") from exc
    return completed.stdout


def certificate_der(cert_path: Path) -> bytes:
    return _run(["openssl", "x509", "-in", str(cert_path), "-outform", "DER"])


def certificate_tls_fingerprint(cert_path: Path) -> str:
    return "sha256:" + hashlib.sha256(certificate_der(cert_path)).hexdigest()


def public_key_der_from_cert(cert_path: Path) -> bytes:
    pem = _run(["openssl", "x509", "-in", str(cert_path), "-pubkey", "-noout"])
    return _run(["openssl", "pkey", "-pubin", "-outform", "DER"], input_data=pem)


def public_key_fingerprint(cert_path: Path) -> str:
    return "sha256:" + hashlib.sha256(public_key_der_from_cert(cert_path)).hexdigest()


def cert_pem(cert_path: Path) -> str:
    return cert_path.read_text(encoding="ascii")


def fingerprint_peer_der(peer_der: bytes) -> str:
    return "sha256:" + hashlib.sha256(peer_der).hexdigest()


@dataclass(frozen=True)
class Identity:
    key_path: Path
    cert_path: Path
    fingerprint: str
    tls_fingerprint: str

    @classmethod
    def load_or_create(cls, paths: Paths, common_name: str) -> "Identity":
        paths.ensure_runtime()
        if paths.identity_key.exists() != paths.identity_cert.exists():
            raise RuntimeError("identity is incomplete; refusing to overwrite existing material")
        if not paths.identity_key.exists():
            _run([
                "openssl", "req", "-x509", "-newkey", "ec",
                "-pkeyopt", "ec_paramgen_curve:prime256v1", "-sha256", "-nodes",
                "-keyout", str(paths.identity_key), "-out", str(paths.identity_cert),
                "-days", "3650", "-subj", f"/CN={_safe_cn(common_name)}",
            ])
            os.chmod(paths.identity_key, 0o600)
            os.chmod(paths.identity_cert, 0o644)
        return cls(
            paths.identity_key, paths.identity_cert,
            public_key_fingerprint(paths.identity_cert),
            certificate_tls_fingerprint(paths.identity_cert),
        )

    def sign(self, data: bytes) -> str:
        signature = _run(["openssl", "dgst", "-sha256", "-sign", str(self.key_path)], input_data=data)
        return base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")


def verify(cert_text: str, data: bytes, signature_text: str, expected_fingerprint: str) -> None:
    with tempfile.TemporaryDirectory(prefix="research-peer-verify-") as temp:
        directory = Path(temp)
        cert = directory / "peer.crt"
        public = directory / "peer.pub"
        signature = directory / "signature.bin"
        cert.write_text(cert_text, encoding="ascii")
        os.chmod(cert, 0o600)
        actual = public_key_fingerprint(cert)
        if actual != expected_fingerprint:
            raise ProtocolError("FINGERPRINT_MISMATCH", "public key fingerprint does not match")
        public.write_bytes(_run(["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"]))
        try:
            signature.write_bytes(base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4)))
        except ValueError as exc:
            raise ProtocolError("AUTH_FAILURE", "signature encoding is invalid") from exc
        completed = subprocess.run(
            ["openssl", "dgst", "-sha256", "-verify", str(public), "-signature", str(signature)],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            raise ProtocolError("AUTH_FAILURE", "message signature is invalid")


def _safe_cn(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch.isalnum() or ch in "._- ").strip()
    return (cleaned or "research-peer")[:64]


def client_tls_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def server_tls_context(identity: Identity) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(identity.cert_path), str(identity.key_path))
    return context

