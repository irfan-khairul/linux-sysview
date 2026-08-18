from unittest.mock import patch
import psutil
from sysview.processes import collect_processes

class FakeMemInfo:
    def __init__(self, rss): self.rss = rss

class FakeProc:
    def __init__(self, pid, name, cpu, mem_pct, rss):
        self.info = {"pid":pid,"name":name,"username":"root","cpu_percent":cpu,
                     "memory_percent":mem_pct,"status":"running",
                     "memory_info":FakeMemInfo(rss),
                     "cmdline":[name,"--flag"] if name else None}

def test_processes_sorted_by_cpu_desc():
    procs=[FakeProc(1,"init",0.5,0.1,1000),FakeProc(2,"hog",90.0,5.0,500000),FakeProc(3,"idle",1.0,0.2,2000)]
    with patch("sysview.processes.psutil.process_iter", return_value=iter(procs)):
        r=collect_processes()
    assert [p["name"] for p in r["processes"]]==["hog","idle","init"]
    assert r["total"]==3
    assert r["processes"][0]["rss"]==500000
    assert r["processes"][0]["cmdline"]=="hog --flag"
    assert r["processes"][0]["user"]=="root"

def test_limit_truncates_but_total_reports_full_count():
    procs=[FakeProc(i,"p%d"%i,float(i),0.1,100) for i in range(10)]
    with patch("sysview.processes.psutil.process_iter", return_value=iter(procs)):
        r=collect_processes(limit=3)
    assert len(r["processes"])==3 and r["total"]==10

def test_vanished_process_is_skipped():
    good=FakeProc(1,"alive",1.0,0.1,100)
    def _iter(attrs=None):
        yield good
        raise psutil.NoSuchProcess(999)
    with patch("sysview.processes.psutil.process_iter", side_effect=_iter):
        r=collect_processes()
    assert [p["name"] for p in r["processes"]]==["alive"]

def test_missing_fields_get_safe_defaults():
    proc=FakeProc(1,None,None,None,0)
    proc.info["username"]=None; proc.info["memory_info"]=None
    with patch("sysview.processes.psutil.process_iter", return_value=iter([proc])):
        r=collect_processes()
    e=r["processes"][0]
    assert e["cpu_percent"]==0.0 and e["user"]=="?" and e["cmdline"]=="" and e["rss"]==0 and e["name"]=="?"
