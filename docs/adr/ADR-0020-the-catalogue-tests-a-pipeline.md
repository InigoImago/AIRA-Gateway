# ADR-0020 — The question catalogue tests a use case's pipeline, not a model

- **Status:** Accepted
- **Date:** 2026-08-16
- **Deciders:** Vadim Scheibe (owner), with the control plane

## Context

`FRD-504` shipped as **model** smoke tests: IT Security keeps a catalogue of questions, picks a
model, and every run is attributed to one seeded use case, `smoke-test`. The shape follows from its
own problem statement — *"none of them says anything about how the models themselves behave"* — and
it answers that question honestly.

It answers only that question, and three things about the built feature said so:

- **The pipeline was never under test.** `FRD-504` §5.3 argued for two modes — *through the
  pipeline* and *direct to the model* — and called the first one "the first honest measurement we
  would have of whether the injection filter earns its place". Only one mode was built, and it was
  the wrong one for that sentence: every run goes to the `smoke-test` use case, whose pipeline is
  empty. So the catalogue has never once exercised a filter, a router or a redactor.
- **The one use case had to be given models by the runner.** `_release_for_testing` wrote
  `allowed_models` on the seeded use case whenever somebody started a run, because `FRD-308` means
  a use case reaches only what was released to it — and the run picked a model the use case had
  never been released. A feature that has to quietly edit a governance decision in order to work is
  a feature fighting the model it is built on. The 2026-08-15 review flagged the role set that may
  do it; the role set was never the problem.
- **The people who most need it could not use it.** A use-case administrator's question is not
  "how does Claude behave" — it is *"does my pipeline hold?"*: does the injection filter catch
  these hundred prompts, does the redactor mangle them, does the router send them somewhere sane.
  There was no way to ask.

Meanwhile the thing needed to ask it already existed. A request to `/uc/<slug>` runs that use
case's pipeline, priced, budgeted, rate-limited and audited. The catalogue is a hundred prompts.
Nothing was missing except a decision about what a run is *about*.

## Decision

**A run is the catalogue put to a use case, through that use case's own pipeline.**

- The **catalogue** stays exactly what it is and stays governed by Global Administrators and IT
  Security: it states what this installation considers an acceptable answer, which is the same kind
  of statement as a global anomaly rule and belongs to the same people.
- A **run names a use case**, not a model. It may be started by somebody the *gateway* would accept
  for that use case **and** who administers it — or by Global Administrator or IT Security, who
  answer for the installation and still need the gateway's acceptance. Its traffic is that use
  case's traffic: its budget, its rate limit, its audit rows.
- **A normal use-case user does not run it** (owner's rule, added 2026-08-16). The first version of
  this decision said "anybody the gateway would accept", on `FRD-504`'s own sentence *whoever may
  call a model may test one* — written when a run was about a model the whole installation had
  approved. A run is now a hundred prompts through somebody's pipeline, spending that use case's
  budget, against a catalogue that states what this installation tests for (`FRD-504` §8). That is
  a decision **about** the use case rather than work **inside** it, and this project already has a
  word for the difference: `may_manage`.
- The use case's **pipeline decides everything else** — which model, whether the prompt is filtered,
  rewritten or refused. That is the point: a blocked question is now a *result*, not a broken run.
- **Testing a model is a use case.** IT Security creates one, releases the models to it, and gives
  its pipeline a start model. Nothing about model testing is a special path any more; it is the
  general mechanism pointed at one model.
- A pipeline therefore declares a **start model**: the model a request enters it at when the caller
  names none. Without one a use case cannot be run, and the console says so rather than guessing.

`_release_for_testing` is deleted. Releasing a model to a use case is its administrator's decision
(`FRD-308`), and it was never this feature's to make.

## Options considered

- **Keep model runs, add pipeline runs beside them.** Two run kinds, two standings, two sets of
  statistics — and the question "how does this stand" would have as many answers as there are
  kinds, which is exactly the reasoning `FRD-504` §5.7 used to refuse grouping the *questions*.
  A model run is a pipeline run whose pipeline starts at that model; keeping both would be keeping
  a special case of the general thing.
- **Let a run name a model *and* a use case.** The smallest change, and it reintroduces the defect:
  a model the use case has not been released is refused at dispatch, so either the run fails or
  something releases it silently. The model a run reaches is the pipeline's decision, and asking
  the caller for it is asking them to predict it.
- **Infer the start model instead of declaring it.** The dry run already tried this
  (`_model_the_pipeline_is_about`), and its own comments record three wrong guesses in a row: the
  first registered model, the first released model, the first released model that can generate.
  Every one of them named a model the operator had not chosen, and reported it as `effective_model`
  where it reads as a decision. A declared field replaces the guess for the dry run too.

## Consequences

**The rule is a composition, not a fourth rule.** `may_run_tests_queryset` is
`may_call_queryset` narrowed by `MANAGE`, because both halves are separately necessary: without the
first the run 403s on its first question, without the second it is a member spending an
administrator's budget. Written as one new predicate it would have been a fourth spelling of an
access rule, which §5 of `LESSONS.md` is a list of.

**Gained.** A use-case administrator can ask whether their pipeline holds, with the same hundred
questions everybody else uses — so answers are comparable across use cases rather than only across
models. A blocked prompt is a first-class outcome. Nothing special-cases test traffic: it is priced
and bounded by the use case that asked for it, which is also where the cost belongs.

**Lost.** A model's standing is no longer read directly off the screen: it is the standing of a use
case whose pipeline starts at that model. For IT Security, that is one use case per model under
evaluation, and the run list names the start model so nobody has to remember. Two runs of one use
case whose start model changed in between are not comparable, and the screen shows the model on the
row rather than pretending otherwise.

**A pipeline gains a field that is not about a step.** `start_model` is where a request *enters*,
which is configuration about the pipeline rather than a stage of it — the same shape `FRD-308`
settled for `allowed_models`, which is a property of the use case rather than an `allow_check` step.
It is validated like every other model a pipeline names: released to the use case, or refused when
the pipeline is saved.

**The seeded `smoke-test` use case survives as an ordinary one.** It is IT Security's
model-evaluation use case, with a released model and a pipeline that starts there — a demonstration
of the general mechanism rather than a place the code knows about. `SMOKE_TEST_USE_CASE` stops
being a constant the application branches on.
