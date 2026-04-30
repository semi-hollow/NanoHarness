from agent_forge.agents.agent_registry import build_default_agents

def run_multi_agent_workflow(task:str)->list[str]:
    agents=build_default_agents()
    order=["PlannerAgent","CodingAgent","TesterAgent","ReviewerAgent"]
    out=[]
    state={"task":task}
    for name in order:
        r=agents[name].run(state)
        state[name]=r.output
        out.append(f"{name}: {r.output}")
    return out
