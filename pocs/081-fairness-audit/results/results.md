# Results: lending model fairness audit (synthetic, planted bias)

Historical approval rate by group (the labels the model learns from): {'A': 0.5326, 'B': 0.2224}. True share of qualified applicants by group (known from the generator): {'A': 0.4938, 'B': 0.5062}. Proxy scan (one-feature AUC for predicting group, 0.5 = no leak): {'income': 0.5081, 'debt_ratio': 0.5096, 'postcode_risk': 0.9238}. Baseline demographic-parity gap 95% bootstrap CI: (0.1647, 0.2422).

| variant | accuracy | dp diff | eq. odds diff | impact ratio | passes 4/5 |
|---|---|---|---|---|---|
| baseline model | 0.703 | 0.209 | 0.099 | 0.531 | NO |
| without proxy feature | 0.677 | 0.027 | 0.164 | 0.926 | yes |
| threshold-optimised for demographic parity | 0.667 | 0.026 | 0.177 | 0.914 | yes |
| threshold-optimised for equalized odds | 0.688 | 0.090 | 0.045 | 0.782 | NO |

Accuracy is measured against the *biased* historical labels, so a fairer model is expected to score lower on it; that gap is the visible cost of the intervention, not a defect.
