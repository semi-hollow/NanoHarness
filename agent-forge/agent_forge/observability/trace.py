import json, time, uuid
from .event import TraceEvent

class TraceRecorder:
    def __init__(self,path:str):
        self.path=path; self.run_id=str(uuid.uuid4()); self.events=[]; self.start=time.time()
    def add(self, **kwargs):
        self.events.append(TraceEvent(run_id=self.run_id, **kwargs).to_dict())
    def write(self):
        with open(self.path,'w',encoding='utf-8') as f: json.dump({"run_id":self.run_id,"events":self.events},f,ensure_ascii=False,indent=2)
