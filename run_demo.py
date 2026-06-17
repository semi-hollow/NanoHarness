"""Small executable wrapper for the Agent Forge CLI.

Keep real startup logic in ``agent_forge.cli``. This file exists so users can
run ``python run_demo.py ...`` from the repository root without remembering the
package module path.
"""

from agent_forge.cli import main


if __name__ == "__main__":
    main()
