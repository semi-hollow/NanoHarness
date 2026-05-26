# Scripts

This folder is for project setup and deterministic verification.

```bash
scripts/setup_macos_local.sh
```

Creates/reuses `.venv`, installs the package in editable mode, and runs
`scripts/verify.sh` on macOS.

```bash
scripts/setup_wsl_local.sh
```

Same idea for Windows WSL/Ubuntu. It stays offline after installation and uses
MockLLM during verification.

```bash
scripts/verify.sh
```

Runs syntax compilation, single/multi/workflow smoke checks, unit tests, and the
lightweight eval runner. It intentionally uses MockLLM so it is safe on company
machines and does not consume DeepSeek quota.
