from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from collections import defaultdict
from typing import Any

log=logging.getLogger('ung.apollo.routes')

class RouteMetrics:
    def __init__(self):
        self._lock=threading.Lock(); self._counters=defaultdict(int); self._timings=defaultdict(lambda:{'count':0,'total_ms':0.0,'max_ms':0.0})
    def increment(self,name:str,value:int=1,**labels:Any)->None:
        key=name + (':' + ','.join(f'{k}={labels[k]}' for k in sorted(labels)) if labels else '')
        with self._lock: self._counters[key]+=value
    def observe(self,name:str,elapsed_ms:float)->None:
        with self._lock:
            v=self._timings[name]; v['count']+=1; v['total_ms']+=float(elapsed_ms); v['max_ms']=max(v['max_ms'],float(elapsed_ms))
    def snapshot(self)->dict[str,Any]:
        with self._lock:
            return {'counters':dict(self._counters),'timings':{k:dict(v) for k,v in self._timings.items()}}

metrics=RouteMetrics()

@contextmanager
def timed_operation(name:str):
    started=time.monotonic()
    try: yield
    finally: metrics.observe(name,(time.monotonic()-started)*1000)

def structured_event(event:str,**numeric:Any)->None:
    safe={k:v for k,v in numeric.items() if isinstance(v,(int,float,bool)) or v is None}
    log.info(json.dumps({'event':event,**safe},sort_keys=True,separators=(',',':')))
