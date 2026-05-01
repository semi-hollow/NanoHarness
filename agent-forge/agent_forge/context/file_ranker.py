from pathlib import Path


def _terms(query: str) -> list[str]:
    return [part.lower() for part in query.replace("/", " ").replace("_", " ").replace("-", " ").split() if part]


def _looks_like_code_task(terms: list[str]) -> bool:
    code_words = {
        "add",
        "bug",
        "class",
        "def",
        "debug",
        "fix",
        "function",
        "implement",
        "method",
        "patch",
        "refactor",
        "test",
    }
    return any(term in code_words for term in terms)


def rank_files(query: str, files: list[str], root: str | Path = ".") -> list[str]:
    root_path = Path(root)
    terms = _terms(query)
    code_task = _looks_like_code_task(terms)

    def score(path: str) -> tuple[int, str]:
        lowered_path = path.lower()
        path_obj = Path(path)
        suffix = path_obj.suffix.lower()
        parts = set(path_obj.parts)
        stem_terms = _terms(path_obj.stem)
        value = 0

        for term in terms:
            if term in lowered_path:
                value += 8
            if term in stem_terms:
                value += 10

        try:
            text = (root_path / path).read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            text = ""

        for term in terms:
            value += min(text.count(term), 5)

        if path.endswith(".py"):
            value += 4
        if "/tests/" in f"/{path}":
            value += 2
        if code_task and suffix == ".py":
            value += 4
        if code_task and ({"src", "agent_forge", "examples"} & parts):
            value += 3
        if code_task and ("docs" in parts or suffix in {".md", ".json"}):
            value -= 6
        if code_task and parts & {"eval_cases"}:
            value -= 4
        if path_obj.name in {"agent_forge_trace.json", "eval_report.md"} or path_obj.name.endswith("_trace.json"):
            value -= 10

        return (-value, path)

    return sorted(files, key=score)
