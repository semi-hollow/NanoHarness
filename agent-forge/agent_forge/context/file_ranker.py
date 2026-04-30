from pathlib import Path


def _terms(query: str) -> list[str]:
    return [part.lower() for part in query.replace("/", " ").replace("_", " ").replace("-", " ").split() if part]


def rank_files(query: str, files: list[str], root: str | Path = ".") -> list[str]:
    root_path = Path(root)
    terms = _terms(query)

    def score(path: str) -> tuple[int, str]:
        lowered_path = path.lower()
        value = 0
        for term in terms:
            if term in lowered_path:
                value += 8
        try:
            text = (root_path / path).read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            text = ""
        for term in terms:
            value += min(text.count(term), 5)
        if path.endswith(".py"):
            value += 1
        if "/tests/" in f"/{path}":
            value += 1
        return (-value, path)

    return sorted(files, key=score)
