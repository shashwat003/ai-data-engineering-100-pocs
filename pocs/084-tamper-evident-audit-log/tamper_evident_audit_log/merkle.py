"""RFC 6962-style Merkle tree: inclusion proofs and consistency proofs (certificate-transparency construction)."""

from __future__ import annotations

import hashlib


def leaf_hash(data: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _split(n: int) -> int:
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def root(leaves: list[bytes]) -> bytes:
    if not leaves:
        return hashlib.sha256(b"").digest()
    if len(leaves) == 1:
        return leaves[0]
    k = _split(len(leaves))
    return node_hash(root(leaves[:k]), root(leaves[k:]))


def inclusion_proof(leaves: list[bytes], index: int) -> list[bytes]:
    if len(leaves) == 1:
        return []
    k = _split(len(leaves))
    if index < k:
        return [*inclusion_proof(leaves[:k], index), root(leaves[k:])]
    return [*inclusion_proof(leaves[k:], index - k), root(leaves[:k])]


def verify_inclusion(
    leaf: bytes, index: int, size: int, proof: list[bytes], expected_root: bytes
) -> bool:
    if index >= size:
        return False
    h, fn, sn = leaf, index, size - 1
    for p in proof:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            h = node_hash(p, h)
            while not fn & 1 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            h = node_hash(h, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and h == expected_root


def consistency_proof(leaves: list[bytes], m: int) -> list[bytes]:
    """Proof that the tree over leaves[:m] is a prefix of the tree over all leaves."""
    return _subproof(m, leaves, True)


def _subproof(m: int, leaves: list[bytes], b: bool) -> list[bytes]:
    n = len(leaves)
    if m == n:
        return [] if b else [root(leaves)]
    k = _split(n)
    if m <= k:
        return [*_subproof(m, leaves[:k], b), root(leaves[k:])]
    return [*_subproof(m - k, leaves[k:], False), root(leaves[:k])]


def verify_consistency(
    old_size: int, new_size: int, old_root: bytes, new_root: bytes, proof: list[bytes]
) -> bool:
    if old_size > new_size or old_size < 0:
        return False
    if old_size == new_size:
        return old_root == new_root and not proof
    if old_size == 0:
        return not proof
    path = list(proof)
    if old_size & (old_size - 1) == 0:
        path = [old_root, *path]
    if not path:
        return False
    fn, sn = old_size - 1, new_size - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1
    fr = sr = path[0]
    for c in path[1:]:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            fr, sr = node_hash(c, fr), node_hash(c, sr)
            while not fn & 1 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            sr = node_hash(sr, c)
        fn >>= 1
        sn >>= 1
    return sn == 0 and fr == old_root and sr == new_root
