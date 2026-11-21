#!/usr/bin/env python3
"""
Suylios Downloader - Ed25519 Imzalama ve Dogrulama Yardimcisi.

Oncelikle `cryptography` kutuphanesini (OpenSSL C-backend) kullanir;
bulunamazsa RFC 8032 uyumlu saf-Python Ed25519 yedegine duser.
Boylece hem CI ortaminda hem kullanici makinesinde sifir ek bagimlilikla calisir.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Tuple

# Suylios Varsayilan Resmi Hotfix Ed25519 Public Key (32 byte hex)
# CI'da HOTFIX_ED25519_PRIVATE_KEY tanimliysa onun karsiligi kullanilir;
# yerel gelistirmede deterministik gelistirici anahtari ile ciftlenir.
DEFAULT_DEV_SEED_HEX = "7375796c696f732d686f746669782d656432353531392d6465762d6b65792d31"


# ---------------------------------------------------------------------------
# Saf Python RFC 8032 Ed25519 (Fallback)
# ---------------------------------------------------------------------------
_B = 256
_Q = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493


def _inv(x: int) -> int:
    return pow(x, _Q - 2, _Q)


_D = -121665 * _inv(121666) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = (x * _I) % _Q
    if x % 2 != 0:
        x = _Q - x
    return x


_BY = 4 * _inv(5) % _Q
_BX = _xrecover(_BY)
_B_PT = (_BX % _Q, _BY % _Q, 1, (_BX * _BY) % _Q)


def _edwards_add(p: tuple, q: tuple) -> tuple:
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _Q
    b = (y1 + x1) * (y2 + x2) % _Q
    c = t1 * 2 * _D * t2 % _Q
    d = z1 * 2 * z2 % _Q
    e = b - a
    f = d - c
    g = d + c
    h = b + a
    return (e * f % _Q, g * h % _Q, f * g % _Q, e * h % _Q)


def _scalarmult(p: tuple, e: int) -> tuple:
    if e == 0:
        return (0, 1, 1, 0)
    q = _scalarmult(p, e // 2)
    q = _edwards_add(q, q)
    if e & 1:
        q = _edwards_add(q, p)
    return q


def _encodepoint(p: tuple) -> bytes:
    x, y, z, _ = p
    zi = _inv(z)
    x = (x * zi) % _Q
    y = (y * zi) % _Q
    bits = [(y >> i) & 1 for i in range(_B - 1)] + [x & 1]
    return bytes(sum(bits[i * 8 + j] << j for j in range(8)) for i in range(_B // 8))


def _decodepoint(s: bytes) -> tuple:
    if len(s) != 32:
        raise ValueError("Invalid Ed25519 point length")
    y = sum(((s[i // 8] >> (i % 8)) & 1) << i for i in range(_B - 1))
    sign = (s[31] >> 7) & 1
    x = _xrecover(y)
    if (x & 1) != sign:
        x = _Q - x
    p = (x, y, 1, (x * y) % _Q)
    if ( -x * x + y * y - 1 - _D * x * x * y * y ) % _Q != 0:
        raise ValueError("Point is not on Ed25519 curve")
    return p


def _py_publickey(seed: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return _encodepoint(_scalarmult(_B_PT, a))


def _py_sign(msg: bytes, seed: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    prefix = h[32:]
    pub = _encodepoint(_scalarmult(_B_PT, a))
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % _L
    r_pt = _encodepoint(_scalarmult(_B_PT, r))
    k = int.from_bytes(hashlib.sha512(r_pt + pub + msg).digest(), "little") % _L
    s = (r + k * a) % _L
    return r_pt + s.to_bytes(32, "little")


def _py_verify(msg: bytes, sig: bytes, pub: bytes) -> bool:
    if len(sig) != 64 or len(pub) != 32:
        return False
    try:
        r_pt = _decodepoint(sig[:32])
        a_pt = _decodepoint(pub)
        s = int.from_bytes(sig[32:], "little")
        if s >= _L:
            return False
        k = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little") % _L
        lhs = _scalarmult(_B_PT, s)
        rhs = _edwards_add(r_pt, _scalarmult(a_pt, k))
        return _encodepoint(lhs) == _encodepoint(rhs)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Birlesik API (cryptography varsa C hizinda, yoksa saf Python)
# ---------------------------------------------------------------------------

def keypair_from_seed(seed_bytes: bytes) -> Tuple[bytes, bytes]:
    """Return (private_seed_32b, public_key_32b)."""
    if len(seed_bytes) != 32:
        raise ValueError("Ed25519 private seed must be exactly 32 bytes")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        priv = Ed25519PrivateKey.from_private_bytes(seed_bytes)
        pub = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return seed_bytes, pub
    except Exception:
        return seed_bytes, _py_publickey(seed_bytes)


def sign_bytes(data: bytes, seed_bytes: bytes) -> bytes:
    """Sign message bytes with 32-byte Ed25519 private seed, returning 64-byte signature."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        priv = Ed25519PrivateKey.from_private_bytes(seed_bytes)
        return priv.sign(data)
    except Exception:
        return _py_sign(data, seed_bytes)


def verify_bytes(data: bytes, signature: bytes, public_key: bytes) -> bool:
    """Verify 64-byte Ed25519 signature against 32-byte public key."""
    if len(signature) != 64 or len(public_key) != 32:
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub = Ed25519PublicKey.from_public_bytes(public_key)
        pub.verify(signature, data)
        return True
    except ImportError:
        return _py_verify(data, signature, public_key)
    except Exception:
        return False


def get_official_public_key_hex() -> str:
    """Return the 32-byte hex public key corresponding to HOTFIX_PUBLIC_KEY or default seed."""
    env_pub = os.environ.get("SUYLIOS_HOTFIX_PUBLIC_KEY", "").strip()
    if len(env_pub) == 64:
        return env_pub.lower()
    _, pub = keypair_from_seed(bytes.fromhex(DEFAULT_DEV_SEED_HEX))
    return pub.hex()


def parse_Signature_text(sig_text: str) -> bytes:
    """Parse hex or base64 signature file content into 64 raw bytes."""
    raw = sig_text.strip().splitlines()[0].strip()
    if len(raw) == 128:
        try:
            return bytes.fromhex(raw)
        except ValueError:
            pass
    return base64.b64decode(raw)
