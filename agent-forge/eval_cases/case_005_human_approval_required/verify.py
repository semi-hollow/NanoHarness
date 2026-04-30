import subprocess, sys, json
from pathlib import Path
trace_path = Path(__file__).with_name('trace.json')
subprocess.run([sys.executable,'run_demo.py','--mode','single','--trace-file',str(trace_path)],check=True)
trace=json.loads(trace_path.read_text(encoding='utf-8'))
ok=any(e.get('event_type')=='human_approval' for e in trace['events'])
raise SystemExit(0 if ok else 1)
