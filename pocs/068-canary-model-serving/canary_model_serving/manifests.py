"""KServe-style InferenceService manifests per rollout stage plus a policy lint. Generated and linted only: no cluster, no CRD validation."""

from __future__ import annotations

import re

import yaml

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
ALLOWED_STEPS = {0, 5, 20, 50, 100}


def inference_service(
    name: str, namespace: str, image: str, canary_percent: int, previous_image: str | None = None
) -> dict:
    spec = {
        "predictor": {
            "canaryTrafficPercent": canary_percent,
            "containers": [
                {
                    "name": "kserve-container",
                    "image": image,
                    "resources": {
                        "requests": {"cpu": "500m", "memory": "512Mi"},
                        "limits": {"cpu": "1", "memory": "1Gi"},
                    },
                    "readinessProbe": {"httpGet": {"path": "/healthz", "port": 8080}},
                }
            ],
        }
    }
    return {
        "apiVersion": "serving.kserve.io/v1beta1",
        "kind": "InferenceService",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app.kubernetes.io/name": name},
            "annotations": {"rollout/previous-image": previous_image or ""},
        },
        "spec": spec,
    }


def lint(doc: dict) -> list[str]:
    out = []
    pred = (doc.get("spec") or {}).get("predictor") or {}
    pct = pred.get("canaryTrafficPercent")
    if pct not in ALLOWED_STEPS:
        out.append(f"canaryTrafficPercent {pct!r} is not one of {sorted(ALLOWED_STEPS)}")
    containers = pred.get("containers") or []
    if not containers:
        out.append("no containers")
    for c in containers:
        img = c.get("image", "")
        if not DIGEST.search(img):
            out.append(f"image {img!r} is not pinned by sha256 digest (tags are mutable)")
        res = c.get("resources", {})
        if not res.get("limits") or not res.get("requests"):
            out.append("container must set resource requests and limits")
        if "readinessProbe" not in c:
            out.append("container needs a readinessProbe so traffic only reaches ready pods")
    if pct and not doc.get("metadata", {}).get("annotations", {}).get("rollout/previous-image"):
        out.append("canary stage needs rollout/previous-image so rollback has a target")
    if doc.get("metadata", {}).get("namespace") in (None, "", "default"):
        out.append("do not deploy into the default namespace")
    return out


def render_stage(name: str, namespace: str, image: str, previous: str, pct: int) -> str:
    return yaml.safe_dump(inference_service(name, namespace, image, pct, previous), sort_keys=False)
