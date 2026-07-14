"""Named benchmark datasets used for reproducible demos and regressions."""

DEFAULT_DATASET = "princeton-nlp/SWE-bench_Lite"
SHOWCASE_INSTANCE_ID = "astropy__astropy-12907"
SHOWCASE_INSTANCE_NOTE = (
    "Astropy nested CompoundModel separability bug. This case is small enough "
    "for local runs but forces real repository checkout, context retrieval, "
    "tool use, patch generation, and trace/usage inspection."
)
REGRESSION_SETS = {
    "core": [
        SHOWCASE_INSTANCE_ID,
        "django__django-11133",
        "matplotlib__matplotlib-18869",
        "pytest-dev__pytest-5103",
        "sympy__sympy-20590",
    ]
}
