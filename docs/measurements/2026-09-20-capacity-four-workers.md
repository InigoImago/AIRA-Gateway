# Load run — the same load with four uvicorn workers

Taken 2026-09-20 01:59 on 8 cores.

`overhead` is the request's wall clock less what the double promised the answer would
take. The baseline column is the same load with the gateway taken out of the path, so
whatever it shows is the driver and the loopback, not the system under test.

| profile | users | rps | ok | failed | p50 ms | p99 ms | overhead p50 | overhead p99 | gw cores | gw CPU ms/req | pg CPU ms/req | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| chat | 50 | 2.22 | 187 | 0 | 2852.6 | 2943.3 | 58.1 | 148.8 | 0.36 | 165.1 | 14.76 | passed: median overhead 58 ms (7 ms of it without the gateway) |
| chat | 150 | 6.28 | 546 | 0 | 2844.6 | 3529.5 | 50.2 | 735.1 | 0.61 | 97.48 | 7.03 | passed: median overhead 50 ms (6 ms of it without the gateway) |
| chat | 300 | 11.86 | 1037 | 0 | 2851.2 | 5062.9 | 56.7 | 2268.4 | 0.93 | 79.06 | 6.57 | passed: median overhead 57 ms (5 ms of it without the gateway) |
| agentic | 50 | 3.32 | 252 | 0 | 5543.6 | 11892.7 | 46.9 | 118.8 | 0.75 | 229.39 | 7.72 | passed: median overhead 47 ms (7 ms of it without the gateway) |
| agentic | 150 | 9.59 | 760 | 0 | 5586.4 | 11997.8 | 64.8 | 234.7 | 1.63 | 171.56 | 5.63 | passed: median overhead 65 ms (4 ms of it without the gateway) |
| agentic | 300 | 18.0 | 1415 | 0 | 7261.3 | 14728.6 | 722.0 | 3062.9 | 2.72 | 152.84 | 9.34 | saturated: median overhead 722 ms above the 250 ms ceiling |
| embed | 4 | 13.21 | 598 | 1 | 287.5 | 383.7 | 165.1 | 261.3 | 1.17 | 89.8 | 4.22 | passed: median overhead 165 ms (89 ms of it without the gateway) |
| embed | 8 | 20.96 | 951 | 39 | 363.8 | 489.4 | 241.4 | 367.0 | 1.86 | 86.91 | 3.75 | saturated: 3.9% of requests failed, most often 'RESOURCE_EXHAUSTED: Request rate limit exceeded for use case.' |

**embed at 4** — failures: {"RESOURCE_EXHAUSTED: Request rate limit exceeded for use case.": 1}; gateway warnings: {}
**embed at 8** — failures: {"RESOURCE_EXHAUSTED: Request rate limit exceeded for use case.": 39}; gateway warnings: {}
