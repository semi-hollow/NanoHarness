import subprocess, sys, json
subprocess.run([sys.executable,'run_demo.py','--mode','single','--trace-file','tmp_trace.json'],check=True)
trace=json.loads(open('tmp_trace.json',encoding='utf-8').read())
ok=any(e.get('event_type')=='human_approval' for e in trace['events'])
raise SystemExit(0 if ok else 1)
