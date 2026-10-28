# Results (measured by `python -m speculative_decoding_demo.main`; toy trigram target and bigram drafts in NumPy; speed-up is MODELLED from call counts)

Vocabulary 30; draft pass cost assumed 0.08 of a target pass (modelled).

## Losslessness: TV distance of the first-2-token distribution from the exact target (k=4)

| draft | TV distance |
|---|---|
| exact copy | 0.025 |
| noisy copy (sigma 0.2) | 0.023 |
| noisy copy (sigma 0.6) | 0.021 |
| bigram summary | 0.021 |
| near-uniform bigram | 0.021 |

Sampling noise floor for the same number of samples: 0.0214 (a lossless sampler should sit at about this level).

Greedy speculative decoding identical to greedy target decoding for every draft: **True**

## Acceptance, tokens per target call and modelled speed-up

| draft | k | acceptance | tokens / target call | theory | modelled speed-up |
|---|---|---|---|---|---|
| exact copy | 1 | 1.000 | 2.000 | 2.000 | 1.850 |
| exact copy | 2 | 1.000 | 3.000 | 3.000 | 2.590 |
| exact copy | 4 | 1.000 | 5.000 | 5.000 | 3.790 |
| exact copy | 8 | 1.000 | 9.000 | 9.000 | 5.490 |
| noisy copy (sigma 0.2) | 1 | 0.931 | 1.930 | 1.930 | 1.790 |
| noisy copy (sigma 0.2) | 2 | 0.927 | 2.790 | 2.790 | 2.400 |
| noisy copy (sigma 0.2) | 4 | 0.931 | 4.350 | 4.350 | 3.300 |
| noisy copy (sigma 0.2) | 8 | 0.931 | 6.910 | 6.860 | 4.190 |
| noisy copy (sigma 0.6) | 1 | 0.801 | 1.800 | 1.800 | 1.670 |
| noisy copy (sigma 0.6) | 2 | 0.797 | 2.430 | 2.430 | 2.100 |
| noisy copy (sigma 0.6) | 4 | 0.788 | 3.280 | 3.280 | 2.490 |
| noisy copy (sigma 0.6) | 8 | 0.793 | 4.250 | 4.230 | 2.580 |
| bigram summary | 1 | 0.333 | 1.330 | 1.330 | 1.230 |
| bigram summary | 2 | 0.338 | 1.450 | 1.450 | 1.250 |
| bigram summary | 4 | 0.330 | 1.490 | 1.490 | 1.130 |
| bigram summary | 8 | 0.332 | 1.500 | 1.500 | 0.910 |
| near-uniform bigram | 1 | 0.308 | 1.310 | 1.310 | 1.210 |
| near-uniform bigram | 2 | 0.304 | 1.400 | 1.400 | 1.200 |
| near-uniform bigram | 4 | 0.310 | 1.450 | 1.450 | 1.100 |
| near-uniform bigram | 8 | 0.316 | 1.460 | 1.460 | 0.890 |
