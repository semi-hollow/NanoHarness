ALLOW=["python -m unittest","python -m unittest discover","git status","git diff"]
DENY=["rm","rm -rf","del","rmdir","git push","git reset --hard","curl","wget","ssh","scp","chmod","chown","powershell Remove-Item","format","mkfs"]
def check_command(cmd:str):
    c=cmd.strip().lower()
    if any(c.startswith(x.lower()) for x in DENY): return False,"dangerous command blocked"
    if any(c.startswith(x.lower()) for x in ALLOW): return True,"allow"
    return False,"not allowlisted"
