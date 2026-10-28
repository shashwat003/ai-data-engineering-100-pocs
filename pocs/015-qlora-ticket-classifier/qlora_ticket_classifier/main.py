"""POC 015: QLoRA mechanics for ticket classification on CPU. REAL: NumPy LoRA/QLoRA training with hand-written backprop, 4-bit
block-quantised frozen base (POC 014 formats), gradient checks. NOT USED: transformers, PEFT, bitsandbytes, GPU, any pretrained LLM."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from pocs_common import write_json
from pocs_common.report import markdown_table

from .experiment import FINE, adapt, fine_data, pretrain, score, tfidf_baseline
from .lora_net import featurize

POC_DIR = Path(__file__).resolve().parent.parent
MODES = ("tfidf+LR (no pretraining)", "linear_probe", "lora", "qlora", "full")


def run(quick: bool = False) -> dict:
    seeds = range(3 if quick else 5)
    sizes = (30, 120) if quick else (30, 60, 120, 240)
    base, coarse_acc = pretrain()
    test_texts, test_y = fine_data(2000, 99)
    xt = featurize(test_texts)
    out: dict = {"pretrain_coarse_train_acc": round(coarse_acc, 4), "grid": {}}
    base_hash = base.base_hash()
    for n in sizes:
        for mode in MODES:
            accs, f1s, trainable = [], [], None
            for s in seeds:
                texts, y = fine_data(n, 1000 + s)
                if mode.startswith("tfidf"):
                    sc = score(tfidf_baseline(texts, y, test_texts), test_y)
                else:
                    m = adapt(base, mode, texts, y, s)
                    sc, trainable = score(m.predict(xt), test_y), m.n_trainable()
                accs.append(sc["acc"])
                f1s.append(sc["macro_f1"])
            out["grid"][f"{n}|{mode}"] = {
                "n": n,
                "mode": mode,
                "acc_mean": round(float(np.mean(accs)), 4),
                "acc_std": round(float(np.std(accs)), 4),
                "f1_mean": round(float(np.mean(f1s)), 4),
                "trainable": trainable,
            }
    out["rank_sweep_n120"] = {}
    for r in (1, 2, 4, 8, 16):
        a = [
            score(adapt(base, "lora", *fine_data(120, 1000 + s), s, rank=r).predict(xt), test_y)[
                "acc"
            ]
            for s in seeds
        ]
        out["rank_sweep_n120"][r] = round(float(np.mean(a)), 4)
    # memory, merge and immutability checks on one model
    texts, y = fine_data(120, 1000)
    q = adapt(base, "qlora", texts, y, 0)
    lo = adapt(base, "lora", texts, y, 0)
    fp32 = base.n_base() * 4
    packed = sum(ly.packed.nbytes for ly in q.layers) + sum(ly.b.nbytes for ly in q.layers)
    adapters = sum(ly.A.nbytes + ly.B.nbytes for ly in q.layers)
    merged = lo.merge()
    out["memory_bytes"] = {
        "base_fp32": fp32,
        "base_q4_0_packed": packed,
        "adapters_fp32": adapters,
        "head_fp32": q.head_W.nbytes + q.head_b.nbytes,
    }
    out["merge"] = {
        "max_abs_logit_diff_lora_vs_merged": float(np.abs(lo.forward(xt[:200]) - merged.forward(xt[:200])).max()),
        "acc_lora": score(lo.predict(xt), test_y)["acc"], "acc_merged": score(merged.predict(xt), test_y)["acc"],
    }  # fmt: skip
    qm = q.merge()
    out["merge"]["acc_qlora_adapter"] = score(q.predict(xt), test_y)["acc"]
    out["merge"]["acc_qlora_merged_dense"] = score(qm.predict(xt), test_y)["acc"]
    qm.quantise_base("Q4_0")
    out["merge"]["acc_qlora_merged_requantised_q4"] = score(qm.predict(xt), test_y)["acc"]
    out["base_unchanged_by_lora_training"] = base.base_hash() == base_hash
    out["fine_classes"] = list(FINE)
    return out


def render(res: dict) -> str:
    rows = {}
    for v in res["grid"].values():
        rows.setdefault(v["n"], {})[v["mode"]] = v
    modes = [m for m in MODES if m in next(iter(rows.values()))]
    mem = res["memory_bytes"]
    md = [
        "# Results (POC 015: REAL NumPy LoRA/QLoRA training on CPU; synthetic tickets; no transformer, PEFT, bitsandbytes or GPU)",
        f"Encoder pretrained on a 3-way COARSE task (train accuracy {res['pretrain_coarse_train_acc']}), then adapted to the 6-way FINE ticket task with few labels. Test: 2,000 unseen synthetic tickets. The generator has confusable phrases, so about 0.75 is the ceiling.",
        "\n## Accuracy by number of labelled tickets (mean +/- std over seeds)\n",
        markdown_table(
            ["labels"] + modes,
            [
                [n]
                + [f"{rows[n][m]['acc_mean']:.3f} +/- {rows[n][m]['acc_std']:.3f}" for m in modes]
                for n in sorted(rows)
            ],
        ),
        "\nTrainable parameters: "
        + ", ".join(
            f"{m}: {next(v['trainable'] for v in res['grid'].values() if v['mode'] == m):,}"
            for m in modes
            if not m.startswith("tfidf")
        )
        + ".",
        "\n## LoRA rank sweep (120 labels)\n",
        markdown_table(
            ["rank"] + [str(r) for r in res["rank_sweep_n120"]],
            [["accuracy"] + list(res["rank_sweep_n120"].values())],
        ),
        "\n## Memory of the frozen base (bytes)\n",
        markdown_table(
            ["fp32 base", "Q4_0 packed base", "ratio", "LoRA adapters (fp32)", "head (fp32)"],
            [
                [
                    mem["base_fp32"],
                    mem["base_q4_0_packed"],
                    f"{mem['base_fp32'] / mem['base_q4_0_packed']:.1f}x",
                    mem["adapters_fp32"],
                    mem["head_fp32"],
                ]
            ],
        ),
        "\n## Merging and immutability\n",
        markdown_table(
            ["check", "value"],
            [[k, f"{v:.3g}" if isinstance(v, float) else v] for k, v in res["merge"].items()]
            + [
                [
                    "pretrained base byte-identical after adapter training",
                    res["base_unchanged_by_lora_training"],
                ]
            ],
        ),
        "",
    ]
    return "\n".join(md)


def main() -> None:
    res = run(bool(os.environ.get("DEMO_QUICK")))
    out = POC_DIR / "results"
    out.mkdir(exist_ok=True)
    write_json(out / "metrics.json", res)
    (out / "results.md").write_text(render(res))
    print(render(res))


if __name__ == "__main__":
    main()
