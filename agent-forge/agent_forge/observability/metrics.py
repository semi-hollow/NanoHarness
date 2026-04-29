def summarize(events:list[dict])->dict:
    return {"tool_call_count":sum(e["event_type"]=="tool_call" for e in events),"steps_count":len(events)}
