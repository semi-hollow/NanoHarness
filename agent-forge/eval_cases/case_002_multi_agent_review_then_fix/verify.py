import subprocess, sys
from pathlib import Path
trace = Path(__file__).with_name('trace.json')
r=subprocess.run([sys.executable,'run_demo.py','--mode','multi','--trace-file',str(trace)],capture_output=True,text=True)
ok='ReviewerAgent' in r.stdout and 'Final' in r.stdout
raise SystemExit(0 if ok and r.returncode==0 else 1)
