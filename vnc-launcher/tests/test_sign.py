import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from launcher import sign

# RFC 8032 7.1 (seed, public, message, signature)
RFC_VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
     "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
     "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
     "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
    ("833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
     "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
     "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
     "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
     "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b589"
     "09351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704"),
]


@pytest.mark.parametrize("seed, pub, msg, sig", RFC_VECTORS)
def test_rfc8032_vectors(seed, pub, msg, sig):
    seed, pub, msg, sig = (bytes.fromhex(x) for x in (seed, pub, msg, sig))
    assert sign.public_key(seed) == pub
    assert sign.sign(seed, msg) == sig
    assert sign.verify(pub, msg, sig)


def test_tampering_is_rejected():
    seed, pub, msg, sig = (bytes.fromhex(x) for x in RFC_VECTORS[2])
    assert not sign.verify(pub, msg + b"\0", sig)                 # 내용이 바뀜
    for i in (0, 31, 32, 63):                                     # 서명 한 비트
        bad = bytearray(sig)
        bad[i] ^= 1
        assert not sign.verify(pub, msg, bytes(bad))
    other = sign.public_key(sign.generate_seed())
    assert not sign.verify(other, msg, sig)                       # 다른 키


def test_bad_input_never_raises():
    seed, pub, msg, sig = (bytes.fromhex(x) for x in RFC_VECTORS[0])
    assert not sign.verify(pub[:31], msg, sig)
    assert not sign.verify(pub, msg, sig[:63])
    assert not sign.verify(pub, msg, sig + b"\0")
    assert not sign.verify(b"\xff" * 32, msg, sig)                # 곡선 위의 점이 아님 (y >= p)
    assert not sign.verify(pub, msg, sig[:32] + b"\xff" * 32)     # S >= L
    s = int.from_bytes(sig[32:], "little") + sign._L               # 같은 서명의 비정규 S
    assert not sign.verify(pub, msg, sig[:32] + s.to_bytes(32, "little"))
    assert not sign.verify(None, msg, sig) and not sign.verify(pub, "문자열", sig)
    with pytest.raises(ValueError):
        sign.sign(b"short", msg)


def test_fingerprint():
    pub = RFC_VECTORS[0][1]
    fp = sign.fingerprint(pub)
    assert len(fp) == 14 and fp.count("-") == 2 and fp == fp.upper()
    assert fp == sign.fingerprint(pub.upper()) == sign.fingerprint(" " + pub + " ")
    assert sign.fingerprint(RFC_VECTORS[1][1]) != fp
    assert sign.fingerprint("") == sign.fingerprint("zz") == sign.fingerprint("ab" * 31) == ""


def test_speed():
    seed = sign.generate_seed()
    pub = sign.public_key(seed)
    t = time.perf_counter()
    sig = sign.sign(seed, b"x" * 500)
    assert sign.verify(pub, b"x" * 500, sig)
    assert time.perf_counter() - t < 1.0        # 느린 PC 에서도 넉넉히 (보통 몇 ms)


def test_matches_cryptography_library():
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.hazmat.primitives import serialization
    for n in range(20):
        seed, msg = os.urandom(32), os.urandom(n * 13)
        key = ed.Ed25519PrivateKey.from_private_bytes(seed)
        pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        assert sign.public_key(seed) == pub
        assert sign.sign(seed, msg) == key.sign(msg)
        assert sign.verify(pub, msg, key.sign(msg))
        key.public_key().verify(sign.sign(seed, msg), msg)          # 반대 방향도 (틀리면 예외)
