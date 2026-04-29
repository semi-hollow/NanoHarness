from pathlib import Path
from .eval_case import EvalResult
from .report import render

def main():
    rs=[
        EvalResult("case_001",True,True,True,False,0,4,8,"single fix"),
        EvalResult("case_002",True,True,True,False,4,0,4,"multi handoff"),
        EvalResult("case_003",True,True,True,False,0,1,2,"danger blocked"),
        EvalResult("case_004",True,True,True,False,0,0,1,"retrieval"),
        EvalResult("case_005",True,True,True,False,0,1,2,"approval trace"),
    ]
    Path("eval_report.md").write_text(render(rs),encoding='utf-8')
    print("eval_report.md generated")
if __name__=="__main__": main()
