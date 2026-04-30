import subprocess, sys
from pathlib import Path
trace = Path(__file__).with_name('trace.json')
subprocess.run([sys.executable,'run_demo.py','--mode','single','--trace-file',str(trace)],check=True)
r=subprocess.run([sys.executable,'-m','unittest','discover','examples/demo_repo/tests','-t','examples/demo_repo'])
raise SystemExit(0 if r.returncode==0 else 1)
