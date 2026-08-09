# Failure triage reference

Use the first stable failure signal as the starting boundary:

1. Record the failing test, exception type, and first project-owned stack frame.
2. Search for the referenced symbol and its nearest callers; do not scan the whole repository by default.
3. State one falsifiable hypothesis before editing.
4. Read the implementation and focused test that can confirm or reject it.
5. If a tool fails because of environment or syntax, repair the tool invocation before changing product code.

Do not treat every failing assertion as a separate bug. Group failures only when code evidence shows a shared cause.
