"""Context 能力包。

外围长期记忆操作统一从 ``agent_forge.context.api`` 进入；Runtime 通过 Context port
请求上下文组装。检索、排序、压缩等实现模块不再从 package root 暴露。
"""
