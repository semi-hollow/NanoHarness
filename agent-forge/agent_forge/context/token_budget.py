def truncate(text,max_chars=2000):
 return text if len(text)<=max_chars else text[:max_chars]+"\n[truncated]"
