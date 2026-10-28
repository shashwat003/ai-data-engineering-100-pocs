"""OPTIONAL recipe for the same task with Hugging Face transformers + PEFT + bitsandbytes (4-bit NF4) on a GPU.

NOT RUN in this repository: torch, transformers, peft, bitsandbytes, a GPU and a downloadable model are not available here, so
nothing below has been executed or measured. It exists so the NumPy hyper-parameters (rank, alpha) map onto the real stack.
`run_recipe()` refuses to start unless every dependency is importable, and even then needs `model_name` to be an already
downloaded model: this code never downloads anything by itself.
"""

from __future__ import annotations

import importlib.util

from .experiment import ALPHA, RANK

REQUIRED = ("torch", "transformers", "peft", "bitsandbytes", "datasets")


class MissingDependency(RuntimeError):
    pass


def missing_dependencies() -> list[str]:
    return [m for m in REQUIRED if importlib.util.find_spec(m) is None]


def lora_config() -> dict:
    """Arguments for `peft.LoraConfig` mirroring the NumPy experiment (adapters on every linear projection)."""
    return {
        "r": RANK,
        "lora_alpha": ALPHA,
        "lora_dropout": 0.05,
        "bias": "none",
        "task_type": "SEQ_CLS",
        "target_modules": ["q_proj", "v_proj"],
    }


def bnb_config() -> dict:
    return {
        "load_in_4bit": True,
        "bnb_4bit_quant_type": "nf4",
        "bnb_4bit_use_double_quant": True,
        "bnb_4bit_compute_dtype": "bfloat16",
    }


def run_recipe(
    model_name: str = "local-model-dir", num_labels: int = 6
) -> None:  # pragma: no cover - needs a GPU stack
    missing = missing_dependencies()
    if missing:
        raise MissingDependency(
            f"{', '.join(missing)} not installed: the Hugging Face QLoRA recipe was not run in this repository"
        )
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForSequenceClassification, BitsAndBytesConfig

    q = BitsAndBytesConfig(**{**bnb_config(), "bnb_4bit_compute_dtype": torch.bfloat16})
    model = AutoModelForSequenceClassification.from_pretrained(
        model_name, num_labels=num_labels, quantization_config=q, local_files_only=True
    )
    model = get_peft_model(prepare_model_for_kbit_training(model), LoraConfig(**lora_config()))
    model.print_trainable_parameters()  # training loop intentionally omitted: untested here
