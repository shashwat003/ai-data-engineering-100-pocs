# Results (POC 019: REAL CPU training of a LoRA router + slot tagger + strict schema gate on synthetic utterances; no language model)
6 tools with JSON Schemas plus abstention; templates for training and testing are DISJOINT families ('unseen templates' = paraphrases never trained on). Averaged over seeds; 200 labelled utterances unless stated.

## Keyword/regex baseline (stands in for an untuned, prompted model)

| test | tool_accuracy | exact_call_rate | invalid_call_rate | wrong_but_valid_rate | hallucinated_calls | clarify_rate |
|---|---|---|---|---|---|---|
| seen templates | 0.992 | 0.240 | 0.673 | 0.079 | 5 | 0.000 |
| unseen templates | 0.840 | 0.164 | 0.771 | 0.020 | 7 | 0.000 |

## Tuned router + tagger, 200 labelled utterances


**router: lora**

| train templates | policy | test | tool_accuracy | exact_call_rate | invalid_call_rate | wrong_but_valid_rate | hallucinated_calls | clarify_rate |
|---|---|---|---|---|---|---|---|---|
| train | tuned | seen templates | 0.986 | 0.976 | 0.024 | 0.000 | 1.667 | 0.000 |
| train | tuned | unseen templates | 0.663 | 0.125 | 0.711 | 0.157 | 2.333 | 0.000 |
| train | gated | seen templates | 0.974 | 0.975 | 0.000 | 0.000 | 0.000 | 0.026 |
| train | gated | unseen templates | 0.345 | 0.119 | 0.000 | 0.555 | 0.000 | 0.633 |
| train+aug | tuned | seen templates | 0.970 | 0.944 | 0.041 | 0.015 | 2.000 | 0.000 |
| train+aug | tuned | unseen templates | 0.622 | 0.334 | 0.576 | 0.071 | 3.000 | 0.000 |
| train+aug | gated | seen templates | 0.958 | 0.942 | 0.000 | 0.016 | 0.000 | 0.042 |
| train+aug | gated | unseen templates | 0.445 | 0.320 | 0.000 | 0.173 | 0.000 | 0.509 |

**router: scratch**

| train templates | policy | test | tool_accuracy | exact_call_rate | invalid_call_rate | wrong_but_valid_rate | hallucinated_calls | clarify_rate |
|---|---|---|---|---|---|---|---|---|
| train | tuned | seen templates | 0.997 | 0.986 | 0.014 | 0.000 | 1.667 | 0.000 |
| train | tuned | unseen templates | 0.803 | 0.137 | 0.605 | 0.229 | 2.333 | 0.000 |
| train | gated | seen templates | 0.985 | 0.986 | 0.000 | 0.000 | 0.000 | 0.015 |
| train | gated | unseen templates | 0.316 | 0.066 | 0.000 | 0.711 | 0.000 | 0.548 |
| train+aug | tuned | seen templates | 0.996 | 0.973 | 0.011 | 0.015 | 2.000 | 0.000 |
| train+aug | tuned | unseen templates | 0.829 | 0.456 | 0.360 | 0.119 | 3.000 | 0.000 |
| train+aug | gated | seen templates | 0.986 | 0.973 | 0.000 | 0.016 | 0.000 | 0.014 |
| train+aug | gated | unseen templates | 0.532 | 0.379 | 0.000 | 0.206 | 0.000 | 0.362 |

## Schema gate audit

527/527 valid gold calls accepted; 446/446 guaranteed-invalid mutations rejected ({'bad_tool': '90/90', 'drop_required': '101/101', 'wrong_type': '79/79', 'extra_key': '90/90', 'out_of_range': '27/27', 'bad_format': '59/59'}); 13/13 malformed raw outputs rejected (truncated, single-quoted, trailing comma, chatty prefix, markdown fence, wrong types, oversize).
