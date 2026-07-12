def retrieve(query: str, docs: list[str], limit: int = 3) -> list[str]:
    """Return lightweight keyword matches for deterministic local retrieval.

    This is intentionally not a vector database. In this project it plays the
    role of a transparent lexical retriever so the context layer can explain
    why a file or doc entered the prompt.
    """

    terms = [part.lower() for part in query.replace("/", " ").replace("_", " ").split() if len(part) > 1]
    scored = []
    for doc in docs:
        lowered = doc.lower()
        score = sum(lowered.count(term) for term in terms)
        if score:
            scored.append((-score, doc))
    return [doc for _, doc in sorted(scored)[:limit]]
