#!/usr/bin/env python3
"""Confere a assinatura de uma versao do Logger, do mesmo jeito que o app do Mac confere.
Uso: python3 tools/updatesig.py [latest.json]   (precisa do pacote 'cryptography'; o Mac nao precisa)
Sai com 0 se a assinatura confere com mac/UPDATE_KEY (ou com --key BASE64)."""
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def message(version, sha, size):
    return ("logger-update:v1\n%s\n%s\n%d" % (version, sha.lower(), size)).encode()


def verify(j, pub_b64):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.b64decode(pub_b64))
    try:
        pub.verify(base64.b64decode(j["sig"]), message(j["version"], j["sha256"], int(j["size"])),
                   ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, KeyError, ValueError):
        return False


if __name__ == "__main__":
    args = sys.argv[1:]
    key = None
    if "--key" in args:
        i = args.index("--key"); key = args[i + 1]; del args[i:i + 2]
    key = key or (ROOT / "mac" / "UPDATE_KEY").read_text().strip()
    j = json.loads(Path(args[0] if args else ROOT / "latest.json").read_text())
    ok = bool(key) and verify(j, key)
    print("assinatura confere" if ok else "ASSINATURA NAO CONFERE")
    sys.exit(0 if ok else 1)
