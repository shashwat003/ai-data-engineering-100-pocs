# Results (measured by `python -m endpoint_autoscaling.main`; discrete-event SIMULATION validated against Erlang C; no SageMaker, no cloud; price assumed)

764,802 requests over 24 h: diurnal load (3 to 25 rps) plus three surprise 10-minute bursts at 2.5x. Instance = 4 workers, mean service 0.4 s (capacity 10 rps), 180 s to start an instance, SLO 1 s, queue timeout 10 s.

## Simulator vs queueing theory (M/M/4, mean wait)

| load | simulated (s) | Erlang C (s) |
|---|---|---|
| lambda=4.0 rps (rho=0.40) | 0.015 | 0.015 |
| lambda=7.0 rps (rho=0.70) | 0.147 | 0.143 |
| lambda=8.5 rps (rho=0.85) | 0.462 | 0.460 |

## Policies

| policy | p95 ms | p99 ms | SLO violations % | dropped % | instance-hours | cost USD (assumed $0.23/h) | scale events |
|---|---|---|---|---|---|---|---|
| fixed 2 instances | 10463.200 | 10688.100 | 51.150 | 8.720 | 48.000 | 11.040 | 0 |
| fixed 6 (peak-sized) | 713.200 | 938.100 | 0.640 | 0.000 | 144.000 | 33.120 | 0 |
| fixed 12 (burst-proof) | 713.000 | 937.900 | 0.640 | 0.000 | 288.000 | 66.240 | 0 |
| target tracking (6 rps/instance) | 777.700 | 7338.100 | 2.530 | 0.080 | 52.600 | 12.100 | 27 |
| step scaling (util 30-70%) | 787.400 | 10133.800 | 2.920 | 0.340 | 54.240 | 12.480 | 64 |
| scheduled floor + target tracking | 716.000 | 942.500 | 0.660 | 0.000 | 70.260 | 16.160 | 14 |
