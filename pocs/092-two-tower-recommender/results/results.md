# Results (measured by `python -m two_tower_recommender.main`; synthetic data, temporal leave-last-2-out, NumPy two-tower, FAISS 1.13)

1500 users, 800 items, 36902 training interactions; two-tower training loss 5.502 -> 5.193 over 2 epochs (chosen on a validation split; validation recall@50 by epoch: {1: 0.3653, 2: 0.4207, 3: 0.4127, 4: 0.416, 5: 0.41, 6: 0.4087, 7: 0.4067, 8: 0.4}).

| model | recall@10 | recall@50 | NDCG@10 | mean popularity rank of top-10 (0=most popular) | coverage@10 | recall@50 on cold items |
|---|---|---|---|---|---|---|
| popularity | 0.100 | 0.268 | 0.066 | 0.009 | 0.028 | 0.000 |
| item-item co-occurrence | 0.175 | 0.416 | 0.114 | 0.031 | 0.281 | 0.000 |
| SVD (32 factors) | 0.144 | 0.359 | 0.097 | 0.098 | 0.395 | 0.000 |
| two-tower | 0.166 | 0.398 | 0.111 | 0.053 | 0.285 | 0.277 |

## ANN retrieval (k=50) vs exact inner-product search

| index | recall vs exact | ms / query |
|---|---|---|
| flat_exact | 1.000 | 0.014 |
| hnsw_ef16 | 0.756 | 0.004 |
| hnsw_ef64 | 1.000 | 0.007 |
| ivf_nprobe1 | 0.334 | 0.006 |
| ivf_nprobe4 | 0.866 | 0.007 |
