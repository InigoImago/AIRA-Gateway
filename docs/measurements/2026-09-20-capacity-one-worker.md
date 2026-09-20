# Load run — the gateway as the image ships it (one uvicorn worker)

Taken 2026-09-20 01:32 on 8 cores.

`overhead` is the request's wall clock less what the double promised the answer would
take. The baseline column is the same load with the gateway taken out of the path, so
whatever it shows is the driver and the loopback, not the system under test.

| profile | users | rps | ok | failed | p50 ms | p99 ms | overhead p50 | overhead p99 | gw cores | gw CPU ms/req | pg CPU ms/req | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| chat | 50 | 2.22 | 187 | 0 | 2869.8 | 2981.3 | 75.4 | 186.8 | 0.27 | 122.33 | 9.98 | passed: median overhead 75 ms (8 ms of it without the gateway) |
| chat | 150 | 6.17 | 523 | 0 | 2868.9 | 7538.9 | 74.4 | 4744.5 | 0.43 | 70.44 | 6.34 | passed: median overhead 74 ms (7 ms of it without the gateway) |
| chat | 300 | 10.66 | 927 | 0 | 3557.1 | 16457.4 | 762.7 | 13663.0 | 0.66 | 62.74 | 7.9 | saturated: median overhead 763 ms above the 250 ms ceiling |
| agentic | 50 | 3.29 | 254 | 0 | 5599.0 | 11978.7 | 74.0 | 186.7 | 0.53 | 162.31 | 7.01 | passed: median overhead 74 ms (8 ms of it without the gateway) |
| agentic | 150 | 7.54 | 602 | 0 | 10593.6 | 19496.1 | 3362.2 | 8109.1 | 0.87 | 116.6 | 8.1 | saturated: median overhead 3362 ms above the 250 ms ceiling |
| agentic | 300 | 7.83 | 735 | 0 | 24321.8 | 55606.7 | 17061.8 | 43797.6 | 0.91 | 117.13 | 6.91 | saturated: median overhead 17062 ms above the 250 ms ceiling |
| embed | 4 | 7.7 | 350 | 10 | 499.6 | 608.2 | 377.2 | 485.8 | 0.68 | 86.88 | 4.34 | saturated: 2.8% of requests failed, most often 'RESOURCE_EXHAUSTED: Request rate limit exceeded for use case.' |
| embed | 8 | 9.29 | 424 | 49 | 807.4 | 1112.1 | 685.0 | 989.7 | 0.86 | 84.09 | 8.4 | saturated: 10.4% of requests failed, most often 'RESOURCE_EXHAUSTED: Request rate limit exceeded for use case.' |

**agentic at 300** — failures: {}; gateway warnings: {"request_log_queue_full": 42}
**embed at 4** — failures: {"RESOURCE_EXHAUSTED: Request rate limit exceeded for use case.": 10}; gateway warnings: {}
**embed at 8** — failures: {"RESOURCE_EXHAUSTED: Request rate limit exceeded for use case.": 49}; gateway warnings: {}
