import subprocess, sys, json
from pathlib import Path
trace='case6_trace.json'
subprocess.run([sys.executable,'run_demo.py','--mode','single','--trace-file',trace],check=True)
obj=json.loads(Path(trace).read_text(encoding='utf-8'))
obs=[e.get('observation','') for e in obj['events'] if e.get('event_type')=='tool_observation']
ok1=any('old text not found' in x for x in obs)
ok2=any('patched once' in x for x in obs)
ok3=any('exit_code=0' in x for x in obs)
raise SystemExit(0 if (ok1 and ok2 and ok3) else 1)
