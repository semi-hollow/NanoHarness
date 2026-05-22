# Study Pack

This folder is the shortest path to understanding Agent Forge for a senior AI Agent interview. It keeps only high-signal material: architecture, runtime flow, context engineering, tool governance, multi-agent design, and interview answers.

Read in this order:

1. `01-code-map-and-architecture.md`
2. `02-agent-loop-context-memory.md`
3. `03-tools-control-safety.md`
4. `04-multi-agent-design.md`
5. `05-interview-playbook.md`
6. `06-interview-question-coverage.md`
7. `07-interview-answer-bank.md`

Run while reading:

```bash
cd /path/to/NanoHarness/agent-forge
source .venv/bin/activate
python run_demo.py --mode single --trace-file trace-single.json
python run_demo.py --mode multi --trace-file trace-multi.json
```

The project deliberately does not implement graph construction, multimodal generation, model training, or a full SWE-bench evaluation stack. Those are interview-adjacent topics, so `05-interview-playbook.md` tells you how to discuss them without polluting the CodingAgent core, and `07-interview-answer-bank.md` gives you concise answers for the full question archive.
