# NanoHarness Benchmark Campaign

## Experiment Identity

- campaign: `verified-commissioning-2-20260726`
- status: `completed`
- source revision: `a275cb29ac4a679d8949e43ae27455d0a2648ee3`
- source branch: `master`
- dirty source allowed: `True`
- dataset/split: `SWE-bench/SWE-bench_Verified` / `test`
- provider/model: `deepseek` / `deepseek-v4-pro`
- temperature: `0.0`
- thinking/effort: `enabled` / `max`
- regression set: `verified-commissioning-2`
- cases: `2`
- repetitions: `1`
- planned runs: `4`
- config digest: `514a7685c7e8988b588c384fb1f29f38b380ea3a62643c3dda619cdfb1f7ee9a`
- provenance note: Post-hoc commissioning subset exported from the first four completed slots of an interrupted Smoke-5 run; it proves the end-to-end evidence flow only and is not a pre-registered success-rate estimate.

Variant order alternates by case and repetition to reduce systematic provider-time bias.
Both variants use the same AgentLoop, model, task, sampling settings, budgets, safety policy and execution mode.

## Runtime Presets

| Variant | Tool visibility | Skills | Scope |
| --- | --- | --- | --- |
| `minimal-control` | `all` | `none` | 同一 AgentLoop、模型、任务、预算和安全边界；暴露完整工具集并关闭 Skill。 |
| `governed-runtime` | `task-aware` | `auto` | 同一 AgentLoop、模型、任务、预算和安全边界；启用 task-aware routing 与内置 Skill。 |

> This is a multi-factor runtime-preset comparison, not a single-factor causal ablation.

## Aggregate Evidence

| Variant | Complete | Candidate patch | Local verified | Official resolved | Tokens | Cost USD | Failed tools |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `minimal-control` | 2/2 | 2/2 | 0/2 | 2/2 (100.0%) | 255328 | 0.111080 | 8 |
| `governed-runtime` | 2/2 | 2/2 | 0/2 | 2/2 (100.0%) | 315759 | 0.135109 | 5 |

## Paired Official Outcomes

- pairs with official outcomes on both variants: `2`
- wins: `{'minimal-control': 0, 'governed-runtime': 0}`
- ties: `2`

## Run Matrix

| # | Case | Repeat | Variant | Run status | Patch | Local | Official | Failure class | Evidence |
| ---: | --- | ---: | --- | --- | --- | --- | --- | --- | --- |
| 1 | `astropy__astropy-12907` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/astropy__astropy-12907__r01__minimal-control/scorecard.json) |
| 2 | `astropy__astropy-12907` | 1 | `governed-runtime` | `completed` | yes | `failed` | `official_resolved` | `official_resolved` | [scorecard](runs/astropy__astropy-12907__r01__governed-runtime/scorecard.json) |
| 3 | `django__django-11133` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-11133__r01__governed-runtime/scorecard.json) |
| 4 | `django__django-11133` | 1 | `minimal-control` | `completed` | yes | `failed` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-11133__r01__minimal-control/scorecard.json) |

## Claim Boundary

- Candidate patch rate uses all planned runs and measures edit reachability, not correctness.
- Official resolved rate uses only explicit resolved/unresolved official reports; missing evaluation is never converted to 0%.
- The two presets intentionally differ in both tool routing and Skill activation, so this campaign evaluates the preset as a whole.
- The selected case set is for mechanism regression or commissioning. It does not estimate SWE-bench Verified population performance or rank models.
- Repetition count is `1`; fewer than three repetitions are commissioning evidence and do not estimate run-to-run stability.
