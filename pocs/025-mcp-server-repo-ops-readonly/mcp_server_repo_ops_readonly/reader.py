"""Data access for the server. The Reader interface has only read methods; there is no write path."""

from __future__ import annotations

import json
import urllib.request
from typing import Protocol

from .data import build


class Reader(Protocol):
    repo: str

    def prs(self) -> list[dict]: ...
    def runs(self) -> list[dict]: ...
    def commits(self) -> list[dict]: ...
    def now(self) -> str: ...


class MockGitHub:
    def __init__(self, seed: int = 4):
        d = build(seed)
        self.repo, self._d = d["repo"], d

    def prs(self):
        return self._d["prs"]

    def runs(self):
        return self._d["runs"]

    def commits(self):
        return self._d["commits"]

    def now(self):
        return self._d["now"]


class HttpGitHubReader:
    """GET-only client for the GitHub REST API, pinned to one repository.

    UNVERIFIED against the real API in this project: no network credentials were used. It exists to
    show the shape of a real adapter; tests cover only that it issues GET requests to allow-listed
    paths and refuses anything else.
    """

    ALLOWED = ("pulls", "actions/runs", "commits")

    def __init__(
        self,
        repo: str,
        token: str | None = None,
        base: str = "https://api.github.com",
        opener=urllib.request.urlopen,
    ):
        if not repo or repo.count("/") != 1 or any(c in repo for c in " ?#&"):
            raise ValueError("repo must look like owner/name")
        self.repo, self._token, self._base, self._open = repo, token, base.rstrip("/"), opener

    def _get(self, path: str) -> object:
        if not path.startswith(self.ALLOWED):
            raise PermissionError(f"path not allowed: {path}")
        req = urllib.request.Request(
            f"{self._base}/repos/{self.repo}/{path}",
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                **({"Authorization": f"Bearer {self._token}"} if self._token else {}),
            },
        )
        with self._open(req, timeout=10) as r:  # noqa: S310
            return json.loads(r.read())

    def prs(self):
        return self._get(
            "pulls?state=all&per_page=50"
        )  # raw GitHub shape; a mapper would normalise it

    def runs(self):
        return self._get("actions/runs?per_page=50")

    def commits(self):
        return self._get("commits?per_page=50")

    def now(self):
        raise NotImplementedError
