def build_context(task,repo_map,memory,tools):
 return f"task:{task}\nrepo:{repo_map[:500]}\nmemory:{memory}\ntools:{tools}"
