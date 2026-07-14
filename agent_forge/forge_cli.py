"""控制台脚本入口；命令解析与分发位于 ``agent_forge.cli``。"""

from agent_forge.cli.dispatch import main

__all__ = ["main"]

if __name__ == "__main__":
    main()
