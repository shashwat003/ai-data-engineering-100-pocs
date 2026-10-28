"""A small file-backed vector store with per-subject encryption (crypto-shredding) and physical deletion."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet, InvalidToken
from idempotent_vector_ingestion.store import HashingEmbedder


class KeyStore:
    """Per-subject data keys. Kept OUTSIDE backups: destroying a key makes every copy of that subject's data unreadable."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.keys: dict[str, str] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get_or_create(self, subject: str) -> Fernet:
        if subject not in self.keys:
            self.keys[subject] = Fernet.generate_key().decode()
            self._save()
        return Fernet(self.keys[subject].encode())

    def get(self, subject: str) -> Fernet | None:
        return Fernet(self.keys[subject].encode()) if subject in self.keys else None

    def destroy(self, subject: str) -> bool:
        existed = self.keys.pop(subject, None) is not None
        self._save()
        return existed

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.keys))
        tmp.replace(self.path)


class TicketStore:
    """Rows are (ticket_id, subject, ciphertext of {text, embedding}); ids and subject ids are stored in the clear."""

    def __init__(self, directory: Path, keys: KeyStore, embedder: HashingEmbedder | None = None):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.keys, self.emb = keys, embedder or HashingEmbedder()
        self.file = self.dir / "tickets.jsonl"

    def _rows(self) -> list[dict]:
        return (
            [json.loads(line) for line in self.file.read_text().splitlines() if line]
            if self.file.exists()
            else []
        )

    def _write(self, rows: list[dict]) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
        tmp.replace(self.file)

    def add(self, ticket_id: str, subject: str, text: str) -> None:
        vec = self.emb.embed([text])[0].astype("float32")
        payload = json.dumps({"text": text, "vec": vec.tolist()}).encode()
        rows = [r for r in self._rows() if r["ticket_id"] != ticket_id]
        rows.append(
            {
                "ticket_id": ticket_id,
                "subject": subject,
                "blob": self.keys.get_or_create(subject).encrypt(payload).decode(),
            }
        )
        self._write(rows)

    def _open(self, row: dict) -> dict | None:
        f = self.keys.get(row["subject"])
        if f is None:
            return None  # key destroyed: unreadable
        try:
            return json.loads(f.decrypt(row["blob"].encode()))
        except InvalidToken:
            return None

    def search(self, query: str, k: int = 3) -> list[tuple[str, float]]:
        q = self.emb.embed([query])[0]
        scored = []
        for r in self._rows():
            p = self._open(r)
            if p:
                scored.append(
                    (r["ticket_id"], float(np.dot(q, np.array(p["vec"], dtype="float32"))))
                )
        return sorted(scored, key=lambda x: -x[1])[:k]

    def count(self, subject: str | None = None) -> int:
        return sum(1 for r in self._rows() if subject is None or r["subject"] == subject)

    def erase_subject(self, subject: str) -> dict:
        rows = self._rows()
        keep = [r for r in rows if r["subject"] != subject]
        self._write(keep)
        return {
            "tickets_deleted": len(rows) - len(keep),
            "key_destroyed": self.keys.destroy(subject),
        }

    def snapshot(self, dest: Path) -> Path:
        """A backup copy of the store files (the keystore is deliberately not included)."""
        shutil.copytree(self.dir, dest)
        return Path(dest)

    @staticmethod
    def readable_in(snapshot_dir: Path, keys: KeyStore, subject: str) -> int:
        """How many of a subject's tickets in a backup can still be decrypted with the keys that exist now."""
        ts = TicketStore(snapshot_dir, keys)
        return sum(1 for r in ts._rows() if r["subject"] == subject and ts._open(r) is not None)


def file_residue(directory: Path, needles: list[str]) -> list[tuple[str, str]]:
    """Raw-byte scan of every file (catches plaintext left in JSON, caches, temp files).

    A needle only counts when it is not embedded in a longer alphanumeric run. Encrypted blobs are random base64, so a
    short identifier such as a user id can appear inside one by chance (about 1 run in 100 for a 4-character id); real
    leaks sit between delimiters (quotes, spaces, '@', newlines) and are still found.
    """
    patterns = [
        (n, re.compile(rb"(?<![A-Za-z0-9])" + re.escape(n.encode()) + rb"(?![A-Za-z0-9])"))
        for n in needles
    ]
    out = []
    for f in sorted(Path(directory).rglob("*")):
        if f.is_file():
            data = f.read_bytes()
            out += [(str(f.relative_to(directory)), n) for n, pat in patterns if pat.search(data)]
    return out
