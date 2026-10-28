"""Append-only SQLite audit log with a hash chain AND a Merkle tree, plus signed checkpoints (Ed25519).

Layers, and what each detects:
- hash chain: edits, deletions, reorderings in the middle of the log
- Merkle tree: lets a third party verify one entry (inclusion) or that today's log extends yesterday's (consistency) without reading the log
- signed checkpoint kept OUTSIDE the database: detects truncation of the tail and whole-log replacement, which a chain alone cannot
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import merkle

GENESIS = "0" * 64


@dataclass(frozen=True)
class Checkpoint:
    size: int
    root: str
    signature: str

    def payload(self) -> bytes:
        return f"audit-log-checkpoint|{self.size}|{self.root}".encode()


class AuditLog:
    def __init__(self, db: sqlite3.Connection, signing_key: Ed25519PrivateKey):
        self.db, self.key = db, signing_key
        db.executescript("""
        CREATE TABLE IF NOT EXISTS entries (seq INTEGER PRIMARY KEY, ts TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL, prev TEXT NOT NULL, hash TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON entries BEGIN SELECT RAISE(ABORT, 'append-only'); END;
        CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON entries BEGIN SELECT RAISE(ABORT, 'append-only'); END;""")

    @staticmethod
    def _canon(seq, ts, actor, action, detail, prev) -> bytes:
        return json.dumps(
            [seq, ts, actor, action, detail, prev], separators=(",", ":"), ensure_ascii=True
        ).encode()

    def append(self, ts: str, actor: str, action: str, detail: dict) -> int:
        row = self.db.execute("SELECT seq, hash FROM entries ORDER BY seq DESC LIMIT 1").fetchone()
        seq, prev = (row[0] + 1, row[1]) if row else (1, GENESIS)
        d = json.dumps(detail, sort_keys=True)
        h = hashlib.sha256(self._canon(seq, ts, actor, action, d, prev)).hexdigest()
        with self.db:
            self.db.execute(
                "INSERT INTO entries VALUES (?,?,?,?,?,?,?)", (seq, ts, actor, action, d, prev, h)
            )
        return seq

    def rows(self):
        return self.db.execute(
            "SELECT seq, ts, actor, action, detail, prev, hash FROM entries ORDER BY seq"
        ).fetchall()

    def leaves(self) -> list[bytes]:
        return [merkle.leaf_hash(bytes.fromhex(r[6])) for r in self.rows()]

    def checkpoint(self) -> Checkpoint:
        leaves = self.leaves()
        r = merkle.root(leaves).hex()
        cp = Checkpoint(len(leaves), r, "")
        return Checkpoint(len(leaves), r, self.key.sign(cp.payload()).hex())

    def inclusion(self, seq: int) -> tuple[bytes, list[bytes], int]:
        leaves = self.leaves()
        return leaves[seq - 1], merkle.inclusion_proof(leaves, seq - 1), len(leaves)

    def consistency(self, old_size: int) -> list[bytes]:
        return merkle.consistency_proof(self.leaves(), old_size)


def verify_chain(rows) -> tuple[bool, int | None]:
    prev = GENESIS
    for seq, ts, actor, action, detail, p, h in rows:
        if (
            p != prev
            or h != hashlib.sha256(AuditLog._canon(seq, ts, actor, action, detail, p)).hexdigest()
        ):
            return False, seq
        prev = h
    return True, None


def verify_checkpoint(cp: Checkpoint, public_key: Ed25519PublicKey) -> bool:
    try:
        public_key.verify(bytes.fromhex(cp.signature), cp.payload())
        return True
    except (InvalidSignature, ValueError):
        return False


def audit(rows, trusted: Checkpoint, public_key: Ed25519PublicKey) -> dict:
    """Full verification of a log against a previously trusted checkpoint (kept outside the DB)."""
    ok_cp = verify_checkpoint(trusted, public_key)
    chain_ok, bad = verify_chain(rows)
    leaves = [merkle.leaf_hash(bytes.fromhex(r[6])) for r in rows]
    n = len(rows)
    prefix_ok = n >= trusted.size and merkle.root(leaves[: trusted.size]).hex() == trusted.root
    return {"checkpoint_signature_ok": ok_cp, "chain_ok": chain_ok, "first_bad_seq": bad, "extends_trusted_checkpoint": prefix_ok,
            "entries": n, "ok": ok_cp and chain_ok and prefix_ok}  # fmt: skip
