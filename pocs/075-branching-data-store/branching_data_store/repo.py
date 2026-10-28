"""Git-like versioned store for data files: content-addressed objects, commits, branches, 3-way merge.

Objects are stored by SHA-256; a commit is a canonical JSON record (parents, path -> object id, message,
timestamp) whose id is the hash of that record. Branch heads live in `refs.json`, replaced atomically.
A pre-merge hook can veto a merge (data validation as a merge gate). Single-process, local filesystem.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,60}$")


class RepoError(RuntimeError):
    pass


class MergeConflict(RepoError):
    def __init__(self, paths: list[str]):
        super().__init__(f"conflicting changes in {paths}")
        self.paths = paths


class HookRejected(RepoError):
    pass


def _h(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def clean_path(p: str) -> str:
    pp = PurePosixPath(p)
    if not p or pp.is_absolute() or ".." in pp.parts or "\\" in p or "\x00" in p or p.endswith("/"):
        raise RepoError(f"unsafe path {p!r}")
    return str(pp)


@dataclass(frozen=True)
class Commit:
    id: str
    parents: tuple[str, ...]
    tree: dict
    message: str
    ts: str


class DataRepo:
    def __init__(self, root: str | Path, clock: Callable[[], str] = lambda: "1970-01-01T00:00:00Z"):
        self.root, self.clock = Path(root), clock
        for d in ("objects", "commits"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        if not (self.root / "refs.json").exists():
            first = self._write_commit((), {}, "init")
            self._save_refs({"main": first.id})

    # --- storage -------------------------------------------------------------------------------
    def _atomic(self, path: Path, data: bytes) -> None:
        fd, tmp = tempfile.mkstemp(dir=path.parent)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)

    def _put_object(self, data: bytes) -> str:
        oid = _h(data)
        p = self.root / "objects" / oid
        if not p.exists():
            self._atomic(p, data)
        return oid

    def _write_commit(self, parents, tree, message) -> Commit:
        body = json.dumps(
            {"parents": list(parents), "tree": tree, "message": message, "ts": self.clock()},
            sort_keys=True,
        ).encode()
        cid = _h(body)
        self._atomic(self.root / "commits" / cid, body)
        return self._read_commit(cid)

    def _read_commit(self, cid: str) -> Commit:
        p = self.root / "commits" / cid
        if not re.fullmatch(r"[0-9a-f]{64}", cid) or not p.exists():
            raise RepoError(f"unknown commit {cid!r}")
        d = json.loads(p.read_bytes())
        return Commit(cid, tuple(d["parents"]), d["tree"], d["message"], d["ts"])

    def _refs(self) -> dict:
        return json.loads((self.root / "refs.json").read_text())

    def _save_refs(self, refs: dict) -> None:
        self._atomic(self.root / "refs.json", json.dumps(refs, sort_keys=True).encode())

    # --- branches ------------------------------------------------------------------------------
    def head(self, branch: str) -> Commit:
        refs = self._refs()
        if branch not in refs:
            raise RepoError(f"unknown branch {branch!r}")
        return self._read_commit(refs[branch])

    def branches(self) -> list[str]:
        return sorted(self._refs())

    def create_branch(self, name: str, source: str = "main") -> None:
        if not BRANCH.match(name) or ".." in name:
            raise RepoError(f"bad branch name {name!r}")
        refs = self._refs()
        if name in refs:
            raise RepoError(f"branch {name!r} exists")
        refs[name] = self.head(source).id
        self._save_refs(refs)

    def commit(self, branch: str, changes: dict[str, bytes | None], message: str) -> Commit:
        """Atomically apply {path: bytes} writes and {path: None} deletes as one commit."""
        if not changes:
            raise RepoError("nothing to commit")
        base = self.head(branch)
        tree = dict(base.tree)
        for path, data in changes.items():
            path = clean_path(path)
            if data is None:
                if path not in tree:
                    raise RepoError(f"cannot delete missing path {path!r}")
                del tree[path]
            else:
                tree[path] = self._put_object(data)
        c = self._write_commit((base.id,), tree, message)
        refs = self._refs()
        refs[branch] = c.id
        self._save_refs(refs)
        return c

    def read(self, ref: str, path: str) -> bytes:
        tree = self.head(ref).tree if ref in self._refs() else self._read_commit(ref).tree
        path = clean_path(path)
        if path not in tree:
            raise RepoError(f"{path!r} not in {ref}")
        return (self.root / "objects" / tree[path]).read_bytes()

    def log(self, branch: str) -> list[Commit]:
        out, seen, stack = [], set(), [self.head(branch).id]
        while stack:
            c = self._read_commit(stack.pop())
            if c.id in seen:
                continue
            seen.add(c.id)
            out.append(c)
            stack.extend(c.parents)
        return out

    def diff(self, a: str, b: str) -> dict[str, str]:
        ta, tb = self.head(a).tree, self.head(b).tree
        d = {p: "added" for p in tb.keys() - ta.keys()} | {
            p: "deleted" for p in ta.keys() - tb.keys()
        }
        d |= {p: "modified" for p in ta.keys() & tb.keys() if ta[p] != tb[p]}
        return dict(sorted(d.items()))

    # --- merge / revert -----------------------------------------------------------------------
    def _ancestors(self, cid: str) -> dict[str, int]:
        dist, frontier, n = {}, [cid], 0
        while frontier:
            nxt = []
            for c in frontier:
                if c in dist:
                    continue
                dist[c] = n
                nxt.extend(self._read_commit(c).parents)
            frontier, n = nxt, n + 1
        return dist

    def merge_base(self, a: str, b: str) -> str:
        da, db = self._ancestors(self.head(a).id), self._ancestors(self.head(b).id)
        common = set(da) & set(db)
        if not common:
            raise RepoError("no common ancestor")
        return min(common, key=lambda c: (da[c] + db[c], c))

    def merge(
        self, src: str, dst: str, *, pre_merge: Callable[[dict[str, bytes]], None] | None = None
    ) -> Commit:
        s, d = self.head(src), self.head(dst)
        base = self._read_commit(self.merge_base(src, dst))
        if base.id == s.id:
            return d  # already up to date
        merged, conflicts = dict(d.tree), []
        for p in set(base.tree) | set(s.tree) | set(d.tree):
            b, ts, td = base.tree.get(p), s.tree.get(p), d.tree.get(p)
            if ts == td or ts == b:
                continue  # same result, or only dst changed it
            if td == b:  # only src changed it
                if ts is None:
                    merged.pop(p, None)
                else:
                    merged[p] = ts
            else:
                conflicts.append(p)
        if conflicts:
            raise MergeConflict(sorted(conflicts))
        if pre_merge is not None:
            try:
                pre_merge({p: (self.root / "objects" / o).read_bytes() for p, o in merged.items()})
            except Exception as exc:
                raise HookRejected(f"pre-merge hook rejected the merge: {exc}") from exc
        if base.id == d.id:  # fast-forward
            refs = self._refs()
            refs[dst] = s.id
            self._save_refs(refs)
            return s
        c = self._write_commit((d.id, s.id), merged, f"merge {src} into {dst}")
        refs = self._refs()
        refs[dst] = c.id
        self._save_refs(refs)
        return c

    def revert(self, branch: str, commit_id: str) -> Commit:
        """New commit that undoes `commit_id` (single-parent commits only); conflicts if later commits touched the same paths."""
        target = self._read_commit(commit_id)
        if len(target.parents) != 1:
            raise RepoError("can only revert single-parent commits")
        parent, cur = self._read_commit(target.parents[0]), self.head(branch)
        tree, conflicts = dict(cur.tree), []
        for p in set(parent.tree) | set(target.tree):
            if parent.tree.get(p) == target.tree.get(p):
                continue
            if cur.tree.get(p) != target.tree.get(p):
                conflicts.append(p)
            elif p in parent.tree:
                tree[p] = parent.tree[p]
            else:
                tree.pop(p, None)
        if conflicts:
            raise MergeConflict(sorted(conflicts))
        c = self._write_commit((cur.id,), tree, f"revert {commit_id[:8]}")
        refs = self._refs()
        refs[branch] = c.id
        self._save_refs(refs)
        return c

    # --- integrity / maintenance --------------------------------------------------------------
    def verify(self) -> list[str]:
        bad = []
        for f in (self.root / "objects").iterdir():
            if _h(f.read_bytes()) != f.name:
                bad.append(f"object {f.name[:12]} content does not match its id")
        for f in (self.root / "commits").iterdir():
            if _h(f.read_bytes()) != f.name:
                bad.append(f"commit {f.name[:12]} does not match its id")
        for br in self.branches():
            for c in self.log(br):
                for p, o in c.tree.items():
                    if not (self.root / "objects" / o).exists():
                        bad.append(f"{br}@{c.id[:8]}: {p} missing object {o[:12]}")
        return sorted(set(bad))

    def gc(self) -> int:
        reachable = {o for br in self.branches() for c in self.log(br) for o in c.tree.values()}
        n = 0
        for f in (self.root / "objects").iterdir():
            if f.name not in reachable:
                f.unlink()
                n += 1
        return n

    def delete_branch(self, name: str) -> None:
        if name == "main":
            raise RepoError("refusing to delete main")
        refs = self._refs()
        if name not in refs:
            raise RepoError(f"unknown branch {name!r}")
        del refs[name]
        self._save_refs(refs)
