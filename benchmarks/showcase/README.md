# NanoHarness Canonical Showcase

This directory is the only current quality surface.

- `quality-selection-protocol-v1.json` freezes the one-time Golden-10 development
  comparison used to select `showcase-quality-v1`.
- `canonical-50-exclusions-v1.json` records every previously seen or selected Case
  excluded before sampling.
- `canonical-50-v1.json` is the deterministic, repository-stratified 50-Case
  sample. Its membership and order were produced without reading issue text,
  patches, tests, difficulty, traces, logs, or outcomes.
- `canonical-showcase-v1.json` is the Workbench/public summary. Pending values stay
  `null`; historical scores never fill a missing current result.

The only public benchmark sentence this directory may eventually support is:

> NanoHarness + `[frozen model/profile]` achieved `X/50` Pass@1 official resolved
> on the deterministic Canonical-50 sample from SWE-bench Verified.

It does not support a claim about the full 500-Case Verified benchmark or an
industry-wide solution rate.
