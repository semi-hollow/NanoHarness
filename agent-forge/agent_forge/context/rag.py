def retrieve(query, docs):
    """Return the first docs containing the query as a tiny deterministic RAG."""

    return [doc for doc in docs if query.lower() in doc.lower()][:3]
