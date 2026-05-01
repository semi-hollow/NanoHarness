# Run Results

## Environment

- Python version: Python 3.11.15
- OS: Darwin Chens-MacBook-Air.local 25.4.0 arm64
- Working directory: `/Users/chenjiahui/repo/NanoHarness/agent-forge`
- Date: 2026-05-01

## Local Run Results

### 1. compileall

Command:

```bash
python3.11 -m compileall agent_forge tests eval_cases examples
```

Result:

- exit code: 0
- summary: Python files under `agent_forge`, `tests`, `eval_cases`, and `examples` compiled successfully.

### 2. single demo

Command:

```bash
python3.11 run_demo.py --mode single
```

Result:

- exit code: 0
- summary: single-agent demo read source/tests, attempted one failing patch, retried successfully, ran unittest, and returned `已完成修复并验证测试通过。`
- trace evidence: `context_assembly`, `plan`, `llm_call`, `action`, `permission_check`, `human_approval`, `tool_call`, `tool_observation`, `observation`, `final_answer`.

### 3. multi demo

Command:

```bash
python3.11 run_demo.py --mode multi
```

Result:

- exit code: 0
- summary: Supervisor handed off to PlannerAgent, CodingAgent, TesterAgent, retried CodingAgent/TesterAgent after the first failure, then handed off to ReviewerAgent.
- final line: `Final: pass; review=review=approved; ...; retry=1`

### 4. workflow demo

Command:

```bash
python3.11 run_demo.py --mode workflow
```

Result:

- exit code: 0
- summary: deterministic workflow returned `WorkflowState(... final_status='success')`.

### 5. unittest

Command:

```bash
python3.11 -m unittest discover tests
```

Result:

- exit code: 0
- summary: `Ran 44 tests ... OK`

### 6. eval runner

Command:

```bash
python3.11 -m agent_forge.eval.eval_runner
```

Result:

- exit code: 0
- summary: `eval_report.md generated`
- eval summary: 19 total, 19 passed, 0 failed, 100.0% pass rate.

## CI Run Results

GitHub Actions workflow has been added at `.github/workflows/agent-forge-ci.yml`. CI result should be verified from GitHub Actions after push.

The workflow uses Python 3.11, enters the `agent-forge` directory, and runs:

```bash
python -m compileall agent_forge tests eval_cases examples
python run_demo.py --mode single
python run_demo.py --mode multi
python run_demo.py --mode workflow
python -m unittest discover tests
python -m agent_forge.eval.eval_runner
```

## Notes

- Commands were run with `python3.11` to avoid the local default `python3` version mismatch.
- Demo commands mutate `examples/demo_repo`, so single and multi demos should be run sequentially rather than in parallel.
