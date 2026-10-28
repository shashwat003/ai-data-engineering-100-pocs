# Results (measured by `python -m llm_to_slm_distillation.main`; synthetic tickets, scikit-learn ensemble teacher, NumPy student; 3 seeds)

250 human-labelled tickets, 6000 unlabelled pool tickets, 2000 test tickets, 6 categories.

| model | test accuracy (mean +/- std) | ECE | student parameters | ms / ticket |
|---|---|---|---|---|
| teacher | 0.738 +/- 0.004 | 0.078 | ensemble | 0.088 |
| student on human labels only | 0.740 +/- 0.012 | 0.057 | 12294.000 | 0.010 |
| student on teacher HARD labels (pool) | 0.753 +/- 0.002 | 0.104 | 12294.000 | 0.010 |
| student on teacher SOFT labels, T=1 (pool) | 0.752 +/- 0.003 | 0.037 | 12294.000 | 0.012 |
| student on teacher SOFT labels, T=2 (pool) | 0.750 +/- 0.003 | 0.162 | 12294.000 | 0.010 |
| student on teacher SOFT labels, T=4 (pool) | 0.750 +/- 0.003 | 0.372 | 12294.000 | 0.010 |
| student on human labels + soft pool | 0.747 +/- 0.004 | 0.150 | 12294.000 | 0.010 |
| oracle: student on true labels of the pool | 0.770 +/- 0.010 | 0.031 | 12294.000 | 0.010 |

## Effect of the human-label budget (2 seeds; accuracy)

| human labels | teacher | student, human labels only | student, distilled (soft, T=1) | oracle student |
|---|---|---|---|---|
| 40 | 0.559 | 0.551 | 0.566 | 0.768 |
| 80 | 0.652 | 0.621 | 0.669 | 0.768 |
| 160 | 0.712 | 0.711 | 0.725 | 0.768 |
| 320 | 0.744 | 0.752 | 0.757 | 0.768 |
