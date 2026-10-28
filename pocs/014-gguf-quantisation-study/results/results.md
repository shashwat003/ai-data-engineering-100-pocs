# Results (measured by `python -m gguf_quantisation_study.main`; llama.cpp-style block formats implemented in NumPy on a small char-level MLP language model, NOT an LLM)

Model: 144,512 weights; FP32 held-out perplexity 1.8939, accuracy 0.7652.

## Uniform quantisation of all weight matrices

| format | bits/weight | perplexity | vs FP32 | accuracy |
|---|---|---|---|---|
| Q8_0 | 8.500 | 1.894 | +0.01% | 0.766 |
| Q5_0 | 5.500 | 1.899 | +0.26% | 0.764 |
| Q5_1 | 6.000 | 1.893 | -0.03% | 0.765 |
| Q4_0 | 4.500 | 1.903 | +0.48% | 0.763 |
| Q4_1 | 5.000 | 1.903 | +0.49% | 0.763 |
| Q3S | 3.500 | 1.904 | +0.52% | 0.763 |
| Q2S | 2.500 | 2.238 | +18.16% | 0.722 |

## Weight reconstruction SQNR (dB, higher is better)

| weights | Q8_0 | Q5_0 | Q5_1 | Q4_0 | Q4_1 | Q3S | Q2S |
|---|---|---|---|---|---|---|---|
| gaussian | 45.400 | 27.400 | 28.400 | 21.300 | 22.100 | 15.200 | 8.800 |
| laplace (heavy tail) | 43.500 | 25.400 | 27.000 | 19.400 | 20.800 | 13.500 | 7.800 |
| gaussian + 0.2% outliers (x25) | 40.000 | 22.400 | 26.400 | 18.300 | 19.700 | 15.200 | 11.300 |

## Sensitivity: only one layer at Q4_0 (perplexity)

- layer 0: 1.8949
- layer 1: 1.8935
- layer 2: 1.8925
- layer 3: 1.9039

## Mixed precision

| plan | effective bits/weight | perplexity | accuracy |
|---|---|---|---|
| uniform Q4_0 | 4.650 | 1.903 | 0.763 |
| output layer Q8_0, rest Q4_0 | 4.740 | 1.893 | 0.765 |
| uniform Q8_0 | 8.650 | 1.894 | 0.766 |

## Modelled file sizes (GB) for hypothetical models, from bits per weight only

| model | F16 | Q8_0 | Q5_0 | Q4_0 | Q4_1 | Q2S |
|---|---|---|---|---|---|---|
| 7B params | 14.000 | 7.440 | 4.810 | 3.940 | 4.380 | 2.190 |
| 13B params | 26.000 | 13.810 | 8.940 | 7.310 | 8.120 | 4.060 |
