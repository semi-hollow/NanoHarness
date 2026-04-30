# 03-tool-calling

Tool calling 是 Agent 从“说”变成“做”的入口。

## Tool Schema

每个工具暴露：

- name
- description
- arguments
- required

schema 让 LLM 知道可用动作，也让 runtime 能做参数校验。

## Tool Registry

`ToolRegistry` 负责：

- 注册工具；
- 暴露 schema；
- unknown tool recovery；
- invalid arguments recovery；
- 捕获工具异常；
- 返回 Observation。

代码：`agent_forge/tools/registry.py`。

## Failure Modes

- unknown tool：模型幻觉出不存在工具；
- invalid arguments：缺少 path/command；
- permission denied：命令或写入不被允许；
- sandbox denied：路径越界或敏感文件；
- execution error：工具内部异常。

面试重点：不要信任模型的 tool call，runtime 必须兜底。
