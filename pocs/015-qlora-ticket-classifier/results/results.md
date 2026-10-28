# Results (POC 015: REAL NumPy LoRA/QLoRA training on CPU; synthetic tickets; no transformer, PEFT, bitsandbytes or GPU)
Encoder pretrained on a 3-way COARSE task (train accuracy 0.9663), then adapted to the 6-way FINE ticket task with few labels. Test: 2,000 unseen synthetic tickets. The generator has confusable phrases, so about 0.75 is the ceiling.

## Accuracy by number of labelled tickets (mean +/- std over seeds)

| labels | tfidf+LR (no pretraining) | linear_probe | lora | qlora | full |
|---|---|---|---|---|---|
| 30 | 0.509 +/- 0.051 | 0.412 +/- 0.021 | 0.527 +/- 0.030 | 0.528 +/- 0.031 | 0.545 +/- 0.048 |
| 60 | 0.652 +/- 0.026 | 0.441 +/- 0.029 | 0.608 +/- 0.033 | 0.608 +/- 0.033 | 0.657 +/- 0.021 |
| 120 | 0.747 +/- 0.014 | 0.499 +/- 0.017 | 0.688 +/- 0.017 | 0.685 +/- 0.018 | 0.733 +/- 0.011 |
| 240 | 0.761 +/- 0.007 | 0.540 +/- 0.027 | 0.722 +/- 0.007 | 0.722 +/- 0.007 | 0.747 +/- 0.003 |

Trainable parameters: linear_probe: 774, lora: 14,086, qlora: 14,086, full: 296,070.

## LoRA rank sweep (120 labels)

| rank | 1 | 2 | 4 | 8 | 16 |
|---|---|---|---|---|---|
| accuracy | 0.627 | 0.644 | 0.670 | 0.688 | 0.678 |

## Memory of the frozen base (bytes)

| fp32 base | Q4_0 packed base | ratio | LoRA adapters (fp32) | head (fp32) |
|---|---|---|---|---|
| 1181184 | 167424 | 7.1x | 53248 | 3096 |

## Merging and immutability

| check | value |
|---|---|
| acc_lora | 0.687 |
| acc_merged | 0.687 |
| acc_qlora_adapter | 0.684 |
| acc_qlora_merged_dense | 0.684 |
| acc_qlora_merged_requantised_q4 | 0.684 |
| max_abs_logit_diff_lora_vs_merged | 5.72e-06 |
| pretrained base byte-identical after adapter training | True |
