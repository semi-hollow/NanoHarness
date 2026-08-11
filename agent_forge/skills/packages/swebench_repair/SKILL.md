---
name: swebench_repair
version: 3.1.0
description: Diagnose one root cause and produce the smallest evidence-backed repository repair.
entrypoint: workflow:swebench_repair
owner: agent-forge
permissions:
  - read:repo
  - write:repo
  - run:validation
dependencies:
  - git:working-tree
  - python:local-venv
tags:
  - coding
  - swe-bench
  - repair
  - validation
activation_terms:
  - swe-bench
  - swebench
required_tools:
  - grep_search
  - read_file
  - replace_text
  - git_diff
  - python_validation
optional_tools:
  - list_files
  - git_status
  - create_file
  - run_command
resources:
  - path: references/failure-triage.md
    description: Focus a failing test or traceback on the first causal code boundary.
    activation_terms:
      - fail
      - failing
      - error
      - traceback
      - pytest
      - test
    max_chars: 1400
  - path: references/validation.md
    description: Choose the narrowest trustworthy validation and report its evidence boundary.
    activation_terms:
      - validate
      - verification
      - regression
      - 验证
      - 回归
    max_chars: 1200
---
# Repository repair workflow

1. Ground one hypothesis in the issue and relevant product source before editing; prefer an existing focused test for evidence.
2. Read only the files needed to confirm or reject that hypothesis.
3. Once the source evidence supports the hypothesis, make the smallest coherent product-source change. A scratch, debug, or test-only diff is not a candidate repair.
4. Do not create or modify disposable validation files unless the task explicitly requires test infrastructure. Inspect the candidate diff, then prefer the narrowest existing validation.
5. If validation fails, use the new evidence to revise the hypothesis instead of repeating the same action.
6. Finish with the candidate change, validation result, uncertainty, and next evidence needed.

The Skill recommends a workflow and tool capabilities. Tool visibility, approval, sandboxing, command policy, and execution remain owned by the Runtime control plane.
