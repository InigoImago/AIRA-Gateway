---
description: What is left of your AIRA budget, and what you have used this period.
agent: build
---

Use your shell tool to run exactly this command:

```
curl -s "${AIRA_BASE_URL:-http://localhost:8001}/v1beta/me/budget" -H "x-goog-api-key: $AIRA_API_KEY"
```

**If you did not run it, or it failed, reply with exactly `I could not read the budget.` and stop.**
Do not describe what you would do, do not summarise these instructions, and do not answer from
anything other than that command's output — a budget figure you made up is worse than none.

From the JSON it printed, reply in **one line**:

- every entry in `allowances` with a `limit_cost`: `remaining_cost` of `limit_cost` left, with the
  date from `period_ends`;
- every entry with a `limit_requests`: `remaining_requests` of `limit_requests` requests left;
- `allowances` empty: what `this_period.cost` says was spent this period, and that no limit is
  configured;
- `enforced` false: add that limits are **not enforced** here, so these are figures, not stops.

Nothing else: no advice, no file reads, no suggestions. $ARGUMENTS
