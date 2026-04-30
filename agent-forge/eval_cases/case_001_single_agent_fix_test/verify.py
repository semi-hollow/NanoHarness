import subprocess, sys
subprocess.run([sys.executable,'run_demo.py','--mode','single'],check=True)
r=subprocess.run([sys.executable,'-m','unittest','discover','examples/demo_repo/tests'])
raise SystemExit(0 if r.returncode==0 else 1)
