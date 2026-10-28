from __future__ import annotations

import random
import tempfile
import time
from pathlib import Path

from . import gitleaks
from .corpus import holdout, negatives, positives
from .scanner import scan_paths


def build_corpus(root: Path, seed: int = 3) -> tuple[dict[str, str], list[str]]:
    rng = random.Random(seed)
    pos, neg = {}, []
    for i, (typ, name, content) in enumerate(holdout(random.Random(seed + 1))):
        (root / "hold").mkdir(parents=True, exist_ok=True)
        (root / f"hold/{i:02d}_{name}").write_text(content)
        pos[f"hold/{i:02d}_{name}"] = "holdout:" + typ
    for i, (typ, name, content) in enumerate(positives(rng)):
        rel = f"pos/{i:02d}_{name}"
        (root / "pos").mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)
        pos[rel] = typ
    for i, (name, content) in enumerate(negatives(rng)):
        rel = f"neg/{i:02d}_{name}"
        (root / "neg").mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)
        neg.append(rel)
    return pos, neg


def run(with_gitleaks: bool = True) -> dict:
    with tempfile.TemporaryDirectory() as t:
        root = Path(t) / "corpus"
        root.mkdir()
        pos, neg = build_corpus(root)
        t0 = time.perf_counter()
        own = scan_paths(sorted(p for p in root.rglob("*") if p.is_file()), root)
        own_s = time.perf_counter() - t0
        own_files = {f.path for f in own}
        tools = {"kit scanner": {"hit": own_files, "seconds": round(own_s, 3)}}
        if with_gitleaks and gitleaks.binary():
            t0 = time.perf_counter()
            gl = gitleaks.scan_dir(root)
            tools["gitleaks " + (gitleaks.version() or "")] = {
                "hit": {str(Path(f["File"]).relative_to(root)) for f in gl},
                "seconds": round(time.perf_counter() - t0, 3),
            }
        out = {}
        for name, d in tools.items():
            tuned = {r: t for r, t in pos.items() if not t.startswith("holdout:")}
            held = {r: t for r, t in pos.items() if t.startswith("holdout:")}
            found = {rel for rel in pos if rel in d["hit"]}
            by_type: dict[str, list[bool]] = {}
            for rel, typ in pos.items():
                by_type.setdefault(typ, []).append(rel in found)
            out[name] = {"recall": round(len(found & set(tuned)) / len(tuned), 3), "holdout_recall": round(len(found & set(held)) / len(held), 3), "holdout_missed": sorted(held[r].split(":")[1] for r in held if r not in found), "false_positive_files": sorted(r for r in neg if r in d["hit"]), "fp_rate": round(sum(r in d["hit"] for r in neg) / len(neg), 3),
                         "missed": sorted(tuned[r] for r in tuned if r not in found), "seconds": d["seconds"]}  # fmt: skip
    return {"positives": len(tuned), "holdout": len(held), "negatives": len(neg), "tools": out}
