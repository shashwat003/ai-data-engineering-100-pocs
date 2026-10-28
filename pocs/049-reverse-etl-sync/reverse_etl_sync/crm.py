"""A mock CRM REST API (FastAPI) with the behaviours that make reverse ETL hard: batch limits, per-record errors,
rate limiting with Retry-After, transient 5xx, idempotency keys, read-only fields. Time is simulated."""

from __future__ import annotations

import re

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
WRITABLE = {"email", "first_name", "last_name", "lifetime_value", "churn_risk", "segment"}
MAX_BATCH = 100


class CRM:
    def __init__(self, requests_per_window: int = 50):
        self.contacts: dict[str, dict] = {}
        self.archived: set[str] = set()
        self.idempotent: dict[str, dict] = {}
        self.budget = self.capacity = requests_per_window
        self.clock = 0.0
        self.fail_queue: list[int] = []
        self.calls = {
            "upsert": 0,
            "archive": 0,
            "rejected_429": 0,
            "failed_5xx": 0,
            "records_written": 0,
        }
        self.app = self._build()

    def advance(self, seconds: float) -> None:
        """Simulated time: the rate-limit window refills every 10 seconds."""
        self.clock += seconds
        if self.clock >= 10:
            self.budget = self.capacity
            self.clock = 0.0

    def fail_next(self, n: int, status: int = 503) -> None:
        self.fail_queue += [status] * n

    def _gate(self):
        if self.fail_queue:
            self.calls["failed_5xx"] += 1
            return JSONResponse(
                {"error": "temporarily unavailable"}, status_code=self.fail_queue.pop(0)
            )
        if self.budget <= 0:
            self.calls["rejected_429"] += 1
            return JSONResponse(
                {"error": "rate limited"},
                status_code=429,
                headers={"Retry-After": str(max(1, int(10 - self.clock)))},
            )
        self.budget -= 1
        return None

    def _build(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None)

        @app.post("/v1/contacts/batch-upsert")
        async def upsert(req: Request, idempotency_key: str | None = Header(default=None)):
            if (g := self._gate()) is not None:
                return g
            body = await req.json()
            recs = body.get("records")
            if not isinstance(recs, list) or not recs or len(recs) > MAX_BATCH:
                return JSONResponse(
                    {"error": f"records must be a list of 1..{MAX_BATCH}"}, status_code=422
                )
            if idempotency_key and idempotency_key in self.idempotent:
                return JSONResponse(self.idempotent[idempotency_key])
            self.calls["upsert"] += 1
            results = []
            for r in recs:
                ext, fields = r.get("external_id"), r.get("fields", {})
                bad = [k for k in fields if k not in WRITABLE]
                if not ext or bad:
                    results.append(
                        {
                            "external_id": ext,
                            "status": "error",
                            "error": f"unknown or read-only fields {bad}"
                            if bad
                            else "missing external_id",
                        }
                    )
                    continue
                if "email" in fields and not EMAIL.match(str(fields["email"])):
                    results.append(
                        {"external_id": ext, "status": "error", "error": "invalid email"}
                    )
                    continue
                cur = self.contacts.get(ext)
                new = {**(cur or {}), **fields}
                if cur is None:
                    self.contacts[ext] = new
                    status = "created"
                elif new == cur:
                    status = "unchanged"
                else:
                    self.contacts[ext] = new
                    status = "updated"
                self.archived.discard(ext)
                if status != "unchanged":
                    self.calls["records_written"] += 1
                results.append({"external_id": ext, "status": status})
            out = {"results": results}
            if idempotency_key:
                self.idempotent[idempotency_key] = out
            return JSONResponse(out)

        @app.post("/v1/contacts/batch-archive")
        async def archive(req: Request, idempotency_key: str | None = Header(default=None)):
            if (g := self._gate()) is not None:
                return g
            body = await req.json()
            ids = body.get("external_ids", [])
            if not isinstance(ids, list) or not ids or len(ids) > MAX_BATCH:
                return JSONResponse({"error": "bad request"}, status_code=422)
            self.calls["archive"] += 1
            for i in ids:
                if i in self.contacts:
                    self.archived.add(i)
            return JSONResponse({"archived": [i for i in ids if i in self.contacts]})

        @app.get("/v1/contacts")
        async def list_(cursor: int = 0, limit: int = 100):
            keys = sorted(self.contacts)
            page = keys[cursor : cursor + min(limit, 200)]
            return {
                "items": [
                    {"external_id": k, **self.contacts[k], "archived": k in self.archived}
                    for k in page
                ],
                "next": cursor + len(page) if cursor + len(page) < len(keys) else None,
            }

        return app
