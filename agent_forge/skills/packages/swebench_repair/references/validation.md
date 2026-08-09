# Validation reference

Choose evidence in this order:

1. The exact failing test named by the task or traceback.
2. The smallest test module covering the changed behavior.
3. A broader regression command only after the focused check passes.

A generated Diff is only a candidate change. Local validation is repository evidence, not an official SWE-bench verdict. If validation cannot run, report the environment blocker and keep the result unverified.
