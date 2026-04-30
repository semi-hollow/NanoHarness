import subprocess, sys
r=subprocess.run([sys.executable,'run_demo.py','--mode','multi'],capture_output=True,text=True)
ok='ReviewerAgent' in r.stdout and 'Final' in r.stdout
raise SystemExit(0 if ok and r.returncode==0 else 1)
