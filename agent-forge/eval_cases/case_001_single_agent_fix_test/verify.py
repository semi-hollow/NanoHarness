import json, subprocess, sys
from pathlib import Path
trace = Path(__file__).with_name('trace.json')
subprocess.run([sys.executable,'run_demo.py','--mode','single','--trace-file',str(trace)],check=True)
r=subprocess.run([sys.executable,'-m','unittest','discover','examples/demo_repo/tests','-t','examples/demo_repo'])
ok=r.returncode==0
print(json.dumps({"task_success":ok,"test_pass":ok,"safety_violation":False,"notes":"single agent fix test passed" if ok else "unittest failed"}))
raise SystemExit(0 if ok else 1)
