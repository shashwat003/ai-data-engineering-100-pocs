"""FastAPI front end for the shadow router."""

from fastapi import FastAPI
from pydantic import BaseModel

from .router import ShadowRouter, assign


class Req(BaseModel):
    user_id: str
    features: dict


def make_app(router: ShadowRouter) -> FastAPI:
    app = FastAPI()

    @app.post("/predict")
    def predict(req: Req):
        return {
            "score": router.predict(req.user_id, req.features),
            "arm": assign(req.user_id, router.exp),
        }

    @app.get("/shadow/summary")
    def summary():
        rows = router.log.rows
        ok = [r for r in rows if r["shadow"] is not None]
        return {
            "requests_shadowed": len(rows),
            "shadow_errors": len(rows) - len(ok),
            "mean_abs_diff": sum(abs(r["prod"] - r["shadow"]) for r in ok) / max(len(ok), 1),
        }

    return app
