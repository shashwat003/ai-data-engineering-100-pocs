"""POC 016: distil a heavy teacher into a tiny student using unlabelled data; compare with training the student on the few human labels."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from pocs_common import write_json
from pocs_common.report import markdown_table

from .data import CATEGORIES, generate
from .models import SoftmaxStudent, ece, make_teacher, one_hot, temper

POC_DIR = Path(__file__).resolve().parent.parent


def accuracy(p: np.ndarray, y: np.ndarray) -> float:
    return float((p.argmax(1) == y).mean())


def run_seed(seed: int, n_labelled: int = 250, n_pool: int = 6000, n_test: int = 2000) -> dict:
    xl, yl = generate(n_labelled, seed)
    xp, yp = generate(
        n_pool, seed + 1000
    )  # the pool's true labels are used ONLY for the oracle upper bound
    xt, yt = generate(n_test, seed + 2000)
    yti = np.array([CATEGORIES.index(c) for c in yt])
    teacher = make_teacher(seed).fit(xl, yl)
    t0 = time.perf_counter()
    pt = teacher.predict_proba(xt)
    teacher_ms = (time.perf_counter() - t0) / len(xt) * 1000
    classes = list(teacher.classes_)
    order = [classes.index(c) for c in CATEGORIES]
    pt = pt[:, order]
    pool_soft = teacher.predict_proba(xp)[:, order]  # teacher labels the unlabelled pool
    res = {
        "teacher": {"accuracy": accuracy(pt, yti), "ece": ece(pt, yti), "ms_per_ticket": teacher_ms}
    }

    def student(name, texts, targets):
        s = SoftmaxStudent(seed=seed).fit(texts, targets)
        t0 = time.perf_counter()
        p = s.predict_proba(xt)
        ms = (time.perf_counter() - t0) / len(xt) * 1000
        res[name] = {
            "accuracy": accuracy(p, yti),
            "ece": ece(p, yti),
            "ms_per_ticket": ms,
            "params": s.n_params,
        }

    student("student on human labels only", xl, one_hot(yl))
    student(
        "student on teacher HARD labels (pool)",
        xp,
        one_hot([CATEGORIES[i] for i in pool_soft.argmax(1)]),
    )
    for T in (1.0, 2.0, 4.0):
        student(f"student on teacher SOFT labels, T={T:g} (pool)", xp, temper(pool_soft, T))
    student(
        "student on human labels + soft pool",
        xl + xp,
        np.vstack([one_hot(yl), temper(pool_soft, 2.0)]),
    )
    student("oracle: student on true labels of the pool", xp, one_hot(yp))
    return res


def run(seeds=(0, 1, 2), **kw) -> dict:
    runs = [run_seed(s, **kw) for s in seeds]
    out = {}
    for name in runs[0]:
        out[name] = {k: round(float(np.mean([r[name][k] for r in runs])), 4) for k in runs[0][name]}
        out[name]["accuracy_std"] = round(float(np.std([r[name]["accuracy"] for r in runs])), 4)
    return {
        "seeds": list(seeds),
        "n_labelled": kw.get("n_labelled", 250),
        "n_pool": kw.get("n_pool", 6000),
        "results": out,
    }


def _mean_acc(runs, name) -> float:
    return round(float(np.mean([r[name]["accuracy"] for r in runs])), 3)


def budget_sweep(budgets=(40, 80, 160, 320), seeds=(0, 1)) -> dict:
    """How much does distillation help as human labels get scarcer?"""
    out = {}
    for n in budgets:
        runs = [run_seed(s, n_labelled=n) for s in seeds]
        out[n] = {
            "teacher": _mean_acc(runs, "teacher"),
            "student on human labels only": _mean_acc(runs, "student on human labels only"),
            "student on teacher SOFT labels, T=1 (pool)": _mean_acc(
                runs, "student on teacher SOFT labels, T=1 (pool)"
            ),
            "oracle": _mean_acc(runs, "oracle: student on true labels of the pool"),
        }
    return out


def main() -> None:
    r = run()
    r["budget_sweep"] = budget_sweep()
    write_json(POC_DIR / "results" / "metrics.json", r)
    rows = [
        [
            k,
            f"{v['accuracy']:.3f} +/- {v['accuracy_std']:.3f}",
            round(v["ece"], 3),
            v.get("params", "ensemble"),
            f"{v['ms_per_ticket']:.3f}",
        ]
        for k, v in r["results"].items()
    ]
    md = ["# Results (measured by `python -m llm_to_slm_distillation.main`; synthetic tickets, scikit-learn ensemble teacher, NumPy student; 3 seeds)", "", f"{r['n_labelled']} human-labelled tickets, {r['n_pool']} unlabelled pool tickets, 2000 test tickets, 6 categories.", "",
          markdown_table(["model", "test accuracy (mean +/- std)", "ECE", "student parameters", "ms / ticket"], rows), "", "## Effect of the human-label budget (2 seeds; accuracy)", "",
          markdown_table(["human labels", "teacher", "student, human labels only", "student, distilled (soft, T=1)", "oracle student"], [[n, *v.values()] for n, v in r["budget_sweep"].items()])]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
