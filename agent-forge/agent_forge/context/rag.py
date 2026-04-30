def retrieve(query, docs):
 return [d for d in docs if query.lower() in d.lower()][:3]
