"""Run the DVC pipeline in an isolated copy of ml_project (no git needed: `dvc init --no-scm`)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

PROJECT = Path(__file__).resolve().parent.parent / "ml_project"
DVC = str(Path(sys.executable).with_name("dvc"))


class Pipeline:
    def __init__(self, workdir: Path):
        self.dir = workdir / "ml_project"
        shutil.copytree(
            PROJECT,
            self.dir,
            ignore=shutil.ignore_patterns(
                "data", "model.pkl", "metrics.json", ".dvc", ".dvcignore", "dvc.lock"
            ),
        )
        self._dvc("init", "--no-scm", "-q")
        self.env_py = sys.executable

    def _dvc(self, *args: str) -> subprocess.CompletedProcess:
        p = subprocess.run(
            [DVC, *args],
            cwd=self.dir,
            capture_output=True,
            text=True,
            env={
                "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
                "DVC_NO_ANALYTICS": "1",
                "HOME": str(self.dir),
                "PYTHONHASHSEED": "0",
            },
        )
        if p.returncode != 0:
            raise RuntimeError(
                f"dvc {' '.join(args)} failed:\n{p.stdout[-800:]}\n{p.stderr[-800:]}"
            )
        return p

    def repro(self) -> dict[str, str]:
        """Returns {stage: 'ran' | 'cached'} parsed from dvc output."""
        out = self._dvc("repro").stdout
        status = {}
        for stage in ("prepare", "train", "evaluate"):
            if f"Stage '{stage}' didn't change, skipping" in out:
                status[stage] = "cached"
            elif f"Running stage '{stage}'" in out:
                status[stage] = "ran"
        return status

    def params(self) -> dict:
        return yaml.safe_load((self.dir / "params.yaml").read_text())

    def set_params(self, **sections) -> None:
        p = self.params()
        for sec, vals in sections.items():
            p[sec].update(vals)
        (self.dir / "params.yaml").write_text(yaml.safe_dump(p))

    def metrics(self) -> dict:
        return json.loads((self.dir / "metrics.json").read_text())

    def file(self, rel: str) -> Path:
        return self.dir / rel
