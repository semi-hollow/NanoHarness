# NanoHarness Benchmark Campaign

## Experiment Identity

- campaign: `swebench-verified-100-v1-a-flash-20260808`
- status: `completed`
- source revision: `34cbe9113bccd62438065cc7ce99af3c653aad4e`
- source branch: `master`
- dirty source allowed: `False`
- dataset/split: `SWE-bench/SWE-bench_Verified` / `test`
- provider/model: `deepseek` / `deepseek-v4-flash`
- temperature: `0.0`
- thinking/effort: `enabled` / `max`
- regression set: `swebench-verified-100-v1:a`
- cases: `50`
- repetitions: `1`
- planned runs: `100`
- config digest: `ec110d094ebdc4c444b8393666b202ae9d164de7b2a600cf7ef38ca37b7c2a1e`
- cohort/shard: `swebench-verified-100-v1` / `a`
- cohort cases/universe: `50` / `500`
- selection: `sha256_rank_without_replacement`
- dataset revision: `91aa3ed51b709be6457e12d00300a6a596d4c6a3`
- cohort SHA-256: `54a82d3454da9ace1674187c78338b76376828475ce8556d0a3e01048ebba239`

Variant order alternates by case and repetition to reduce systematic provider-time bias.
Both variants use the same AgentLoop, model, task, sampling settings, budgets, safety policy and execution mode.

## Runtime Presets

| Variant | Tool visibility | Skills | Scope |
| --- | --- | --- | --- |
| `minimal-control` | `all` | `none` | 同一 AgentLoop、模型、任务、预算和安全边界；暴露完整工具集并关闭 Skill。 |
| `governed-runtime` | `task-aware` | `auto` | 同一 AgentLoop、模型、任务、预算和安全边界；启用 task-aware routing 与内置 Skill。 |

> This is a multi-factor runtime-preset comparison, not a single-factor causal ablation.

## Aggregate Evidence

| Variant | Complete | Candidate patch | Local verified candidate | Official resolved / selected | Accepted / evaluated patch | Infra | Failed tools | Execution cost USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `minimal-control` | 50/50 | 32/50 | 0/50 | 20/50 (40.0%) | 20/32 (62.5%) | 0 | 36/993 | 1.242115 |
| `governed-runtime` | 50/50 | 27/50 | 0/50 | 14/50 (28.0%) | 14/27 (51.9%) | 1 | 14/973 | 1.124990 |

## Paired Official Outcomes

- pairs with official outcomes on both variants: `25`
- wins: `{'minimal-control': 4, 'governed-runtime': 0}`
- ties: `21`

## Paired Selected-Sample Outcomes

- adjudicated pairs: `49`
- pairs excluded for infrastructure failure: `1`
- wins: `{'minimal-control': 6, 'governed-runtime': 1}`
- ties: `42`

## Run Matrix

| # | Case | Repeat | Variant | Run status | Patch | Local | Official | Failure class | Evidence |
| ---: | --- | ---: | --- | --- | --- | --- | --- | --- | --- |
| 1 | `django__django-11087` | 1 | `minimal-control` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-11087__r01__minimal-control/scorecard.json) |
| 2 | `django__django-11087` | 1 | `governed-runtime` | `completed` | yes | `failed` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/django__django-11087__r01__governed-runtime/scorecard.json) |
| 3 | `matplotlib__matplotlib-23299` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/matplotlib__matplotlib-23299__r01__governed-runtime/scorecard.json) |
| 4 | `matplotlib__matplotlib-23299` | 1 | `minimal-control` | `completed` | no | `failed` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/matplotlib__matplotlib-23299__r01__minimal-control/scorecard.json) |
| 5 | `django__django-11141` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_eval_failed` | `validation_environment_unavailable` | [scorecard](runs/django__django-11141__r01__minimal-control/scorecard.json) |
| 6 | `django__django-11141` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/django__django-11141__r01__governed-runtime/scorecard.json) |
| 7 | `django__django-12209` | 1 | `governed-runtime` | `completed` | no | `failed` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-12209__r01__governed-runtime/scorecard.json) |
| 8 | `django__django-12209` | 1 | `minimal-control` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-12209__r01__minimal-control/scorecard.json) |
| 9 | `django__django-13449` | 1 | `minimal-control` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-13449__r01__minimal-control/scorecard.json) |
| 10 | `django__django-13449` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-13449__r01__governed-runtime/scorecard.json) |
| 11 | `matplotlib__matplotlib-13989` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/matplotlib__matplotlib-13989__r01__governed-runtime/scorecard.json) |
| 12 | `matplotlib__matplotlib-13989` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/matplotlib__matplotlib-13989__r01__minimal-control/scorecard.json) |
| 13 | `django__django-15930` | 1 | `minimal-control` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-15930__r01__minimal-control/scorecard.json) |
| 14 | `django__django-15930` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-15930__r01__governed-runtime/scorecard.json) |
| 15 | `sphinx-doc__sphinx-10323` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sphinx-doc__sphinx-10323__r01__governed-runtime/scorecard.json) |
| 16 | `sphinx-doc__sphinx-10323` | 1 | `minimal-control` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sphinx-doc__sphinx-10323__r01__minimal-control/scorecard.json) |
| 17 | `django__django-13028` | 1 | `minimal-control` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-13028__r01__minimal-control/scorecard.json) |
| 18 | `django__django-13028` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-13028__r01__governed-runtime/scorecard.json) |
| 19 | `django__django-12050` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-12050__r01__governed-runtime/scorecard.json) |
| 20 | `django__django-12050` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-12050__r01__minimal-control/scorecard.json) |
| 21 | `django__django-15731` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-15731__r01__minimal-control/scorecard.json) |
| 22 | `django__django-15731` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-15731__r01__governed-runtime/scorecard.json) |
| 23 | `pytest-dev__pytest-7205` | 1 | `governed-runtime` | `completed` | yes | `failed` | `official_resolved` | `official_resolved` | [scorecard](runs/pytest-dev__pytest-7205__r01__governed-runtime/scorecard.json) |
| 24 | `pytest-dev__pytest-7205` | 1 | `minimal-control` | `completed` | yes | `failed` | `official_resolved` | `official_resolved` | [scorecard](runs/pytest-dev__pytest-7205__r01__minimal-control/scorecard.json) |
| 25 | `sphinx-doc__sphinx-7748` | 1 | `minimal-control` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sphinx-doc__sphinx-7748__r01__minimal-control/scorecard.json) |
| 26 | `sphinx-doc__sphinx-7748` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sphinx-doc__sphinx-7748__r01__governed-runtime/scorecard.json) |
| 27 | `pydata__xarray-3993` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_eval_failed` | `validation_environment_unavailable` | [scorecard](runs/pydata__xarray-3993__r01__governed-runtime/scorecard.json) |
| 28 | `pydata__xarray-3993` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_eval_failed` | `validation_environment_unavailable` | [scorecard](runs/pydata__xarray-3993__r01__minimal-control/scorecard.json) |
| 29 | `scikit-learn__scikit-learn-15100` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/scikit-learn__scikit-learn-15100__r01__minimal-control/scorecard.json) |
| 30 | `scikit-learn__scikit-learn-15100` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/scikit-learn__scikit-learn-15100__r01__governed-runtime/scorecard.json) |
| 31 | `django__django-17029` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-17029__r01__governed-runtime/scorecard.json) |
| 32 | `django__django-17029` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-17029__r01__minimal-control/scorecard.json) |
| 33 | `sympy__sympy-20438` | 1 | `minimal-control` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sympy__sympy-20438__r01__minimal-control/scorecard.json) |
| 34 | `sympy__sympy-20438` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sympy__sympy-20438__r01__governed-runtime/scorecard.json) |
| 35 | `django__django-13344` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-13344__r01__governed-runtime/scorecard.json) |
| 36 | `django__django-13344` | 1 | `minimal-control` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-13344__r01__minimal-control/scorecard.json) |
| 37 | `sympy__sympy-20590` | 1 | `minimal-control` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sympy__sympy-20590__r01__minimal-control/scorecard.json) |
| 38 | `sympy__sympy-20590` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sympy__sympy-20590__r01__governed-runtime/scorecard.json) |
| 39 | `sphinx-doc__sphinx-9230` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sphinx-doc__sphinx-9230__r01__governed-runtime/scorecard.json) |
| 40 | `sphinx-doc__sphinx-9230` | 1 | `minimal-control` | `completed` | no | `unavailable` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sphinx-doc__sphinx-9230__r01__minimal-control/scorecard.json) |
| 41 | `django__django-12965` | 1 | `minimal-control` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-12965__r01__minimal-control/scorecard.json) |
| 42 | `django__django-12965` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-12965__r01__governed-runtime/scorecard.json) |
| 43 | `scikit-learn__scikit-learn-14629` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/scikit-learn__scikit-learn-14629__r01__governed-runtime/scorecard.json) |
| 44 | `scikit-learn__scikit-learn-14629` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/scikit-learn__scikit-learn-14629__r01__minimal-control/scorecard.json) |
| 45 | `django__django-14007` | 1 | `minimal-control` | `completed` | yes | `not_run` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/django__django-14007__r01__minimal-control/scorecard.json) |
| 46 | `django__django-14007` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_eval_failed` | `validation_environment_unavailable` | [scorecard](runs/django__django-14007__r01__governed-runtime/scorecard.json) |
| 47 | `django__django-10097` | 1 | `governed-runtime` | `completed` | yes | `failed` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/django__django-10097__r01__governed-runtime/scorecard.json) |
| 48 | `django__django-10097` | 1 | `minimal-control` | `completed` | yes | `failed` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/django__django-10097__r01__minimal-control/scorecard.json) |
| 49 | `matplotlib__matplotlib-22871` | 1 | `minimal-control` | `completed` | no | `failed` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/matplotlib__matplotlib-22871__r01__minimal-control/scorecard.json) |
| 50 | `matplotlib__matplotlib-22871` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/matplotlib__matplotlib-22871__r01__governed-runtime/scorecard.json) |
| 51 | `django__django-11490` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-11490__r01__governed-runtime/scorecard.json) |
| 52 | `django__django-11490` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/django__django-11490__r01__minimal-control/scorecard.json) |
| 53 | `matplotlib__matplotlib-24026` | 1 | `minimal-control` | `completed` | yes | `failed` | `official_resolved` | `official_resolved` | [scorecard](runs/matplotlib__matplotlib-24026__r01__minimal-control/scorecard.json) |
| 54 | `matplotlib__matplotlib-24026` | 1 | `governed-runtime` | `completed` | yes | `failed` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/matplotlib__matplotlib-24026__r01__governed-runtime/scorecard.json) |
| 55 | `sphinx-doc__sphinx-7440` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sphinx-doc__sphinx-7440__r01__governed-runtime/scorecard.json) |
| 56 | `sphinx-doc__sphinx-7440` | 1 | `minimal-control` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sphinx-doc__sphinx-7440__r01__minimal-control/scorecard.json) |
| 57 | `sphinx-doc__sphinx-9658` | 1 | `minimal-control` | `completed` | yes | `not_run` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/sphinx-doc__sphinx-9658__r01__minimal-control/scorecard.json) |
| 58 | `sphinx-doc__sphinx-9658` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sphinx-doc__sphinx-9658__r01__governed-runtime/scorecard.json) |
| 59 | `sympy__sympy-17655` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sympy__sympy-17655__r01__governed-runtime/scorecard.json) |
| 60 | `sympy__sympy-17655` | 1 | `minimal-control` | `completed` | yes | `failed` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/sympy__sympy-17655__r01__minimal-control/scorecard.json) |
| 61 | `django__django-16560` | 1 | `minimal-control` | `completed` | yes | `not_run` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-16560__r01__minimal-control/scorecard.json) |
| 62 | `django__django-16560` | 1 | `governed-runtime` | `completed` | yes | `not_run` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/django__django-16560__r01__governed-runtime/scorecard.json) |
| 63 | `matplotlib__matplotlib-25960` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/matplotlib__matplotlib-25960__r01__governed-runtime/scorecard.json) |
| 64 | `matplotlib__matplotlib-25960` | 1 | `minimal-control` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/matplotlib__matplotlib-25960__r01__minimal-control/scorecard.json) |
| 65 | `sympy__sympy-12489` | 1 | `minimal-control` | `completed` | yes | `not_run` | `official_resolved` | `official_resolved` | [scorecard](runs/sympy__sympy-12489__r01__minimal-control/scorecard.json) |
| 66 | `sympy__sympy-12489` | 1 | `governed-runtime` | `completed` | yes | `failed` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/sympy__sympy-12489__r01__governed-runtime/scorecard.json) |
| 67 | `scikit-learn__scikit-learn-10844` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/scikit-learn__scikit-learn-10844__r01__governed-runtime/scorecard.json) |
| 68 | `scikit-learn__scikit-learn-10844` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/scikit-learn__scikit-learn-10844__r01__minimal-control/scorecard.json) |
| 69 | `django__django-15375` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-15375__r01__minimal-control/scorecard.json) |
| 70 | `django__django-15375` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-15375__r01__governed-runtime/scorecard.json) |
| 71 | `django__django-11451` | 1 | `governed-runtime` | `completed` | yes | `failed` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-11451__r01__governed-runtime/scorecard.json) |
| 72 | `django__django-11451` | 1 | `minimal-control` | `completed` | yes | `failed` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-11451__r01__minimal-control/scorecard.json) |
| 73 | `django__django-16082` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-16082__r01__minimal-control/scorecard.json) |
| 74 | `django__django-16082` | 1 | `governed-runtime` | `completed` | no | `unavailable` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-16082__r01__governed-runtime/scorecard.json) |
| 75 | `django__django-11848` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_eval_failed` | `validation_environment_unavailable` | [scorecard](runs/django__django-11848__r01__governed-runtime/scorecard.json) |
| 76 | `django__django-11848` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_eval_failed` | `validation_environment_unavailable` | [scorecard](runs/django__django-11848__r01__minimal-control/scorecard.json) |
| 77 | `sphinx-doc__sphinx-7462` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_eval_failed` | `validation_environment_unavailable` | [scorecard](runs/sphinx-doc__sphinx-7462__r01__minimal-control/scorecard.json) |
| 78 | `sphinx-doc__sphinx-7462` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_eval_failed` | `validation_environment_unavailable` | [scorecard](runs/sphinx-doc__sphinx-7462__r01__governed-runtime/scorecard.json) |
| 79 | `django__django-11239` | 1 | `governed-runtime` | `completed` | yes | `failed` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/django__django-11239__r01__governed-runtime/scorecard.json) |
| 80 | `django__django-11239` | 1 | `minimal-control` | `completed` | yes | `failed` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-11239__r01__minimal-control/scorecard.json) |
| 81 | `django__django-16429` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-16429__r01__minimal-control/scorecard.json) |
| 82 | `django__django-16429` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-16429__r01__governed-runtime/scorecard.json) |
| 83 | `django__django-14376` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_eval_failed` | `validation_environment_unavailable` | [scorecard](runs/django__django-14376__r01__governed-runtime/scorecard.json) |
| 84 | `django__django-14376` | 1 | `minimal-control` | `completed` | yes | `not_run` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/django__django-14376__r01__minimal-control/scorecard.json) |
| 85 | `sympy__sympy-14531` | 1 | `minimal-control` | `completed` | yes | `failed` | `official_resolved` | `official_resolved` | [scorecard](runs/sympy__sympy-14531__r01__minimal-control/scorecard.json) |
| 86 | `sympy__sympy-14531` | 1 | `governed-runtime` | `completed` | yes | `not_run` | `official_resolved` | `official_resolved` | [scorecard](runs/sympy__sympy-14531__r01__governed-runtime/scorecard.json) |
| 87 | `sympy__sympy-21612` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sympy__sympy-21612__r01__governed-runtime/scorecard.json) |
| 88 | `sympy__sympy-21612` | 1 | `minimal-control` | `completed` | yes | `failed` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/sympy__sympy-21612__r01__minimal-control/scorecard.json) |
| 89 | `sphinx-doc__sphinx-11510` | 1 | `minimal-control` | `completed` | no | `passed` | `official_eval_skipped_empty_patch` | `locally_verified_candidate` | [scorecard](runs/sphinx-doc__sphinx-11510__r01__minimal-control/scorecard.json) |
| 90 | `sphinx-doc__sphinx-11510` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/sphinx-doc__sphinx-11510__r01__governed-runtime/scorecard.json) |
| 91 | `pytest-dev__pytest-5809` | 1 | `governed-runtime` | `completed` | yes | `failed` | `official_resolved` | `official_resolved` | [scorecard](runs/pytest-dev__pytest-5809__r01__governed-runtime/scorecard.json) |
| 92 | `pytest-dev__pytest-5809` | 1 | `minimal-control` | `completed` | yes | `failed` | `official_resolved` | `official_resolved` | [scorecard](runs/pytest-dev__pytest-5809__r01__minimal-control/scorecard.json) |
| 93 | `django__django-13809` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-13809__r01__minimal-control/scorecard.json) |
| 94 | `django__django-13809` | 1 | `governed-runtime` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-13809__r01__governed-runtime/scorecard.json) |
| 95 | `django__django-13837` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `provider_transport_error` | [scorecard](runs/django__django-13837__r01__governed-runtime/scorecard.json) |
| 96 | `django__django-13837` | 1 | `minimal-control` | `completed` | yes | `unavailable` | `official_resolved` | `official_resolved` | [scorecard](runs/django__django-13837__r01__minimal-control/scorecard.json) |
| 97 | `psf__requests-2317` | 1 | `minimal-control` | `completed` | yes | `failed` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/psf__requests-2317__r01__minimal-control/scorecard.json) |
| 98 | `psf__requests-2317` | 1 | `governed-runtime` | `completed` | yes | `failed` | `official_eval_failed` | `official_eval_failed` | [scorecard](runs/psf__requests-2317__r01__governed-runtime/scorecard.json) |
| 99 | `django__django-12663` | 1 | `governed-runtime` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-12663__r01__governed-runtime/scorecard.json) |
| 100 | `django__django-12663` | 1 | `minimal-control` | `completed` | no | `not_run` | `official_eval_skipped_empty_patch` | `pending_tool_call_at_stop` | [scorecard](runs/django__django-12663__r01__minimal-control/scorecard.json) |

## Claim Boundary

- Candidate patch rate uses all planned runs and measures edit reachability, not correctness.
- Official resolved / selected uses every pre-registered case as the denominator; a stable no-patch run is unresolved.
- Accepted / evaluated patch only measures official acceptance among patches that reached an explicit evaluator verdict.
- A provider or evaluator infrastructure failure is retried once, then disclosed and excluded from adjudicated pair claims.
- The two presets intentionally differ in both tool routing and Skill activation, so this campaign evaluates the preset as a whole.
- Report the result as the resolved rate on this pre-registered deterministic sample. Sampling error, one run per case and provider behavior prevent treating it as an official leaderboard score.
- Each of the `50` cases was run once per preset. This measures sample coverage, but it does not estimate run-to-run stochastic stability.
