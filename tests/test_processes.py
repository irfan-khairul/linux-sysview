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
                     "cmdline":[name,"--flag"] if name else None,
                     "create_time":1000.0+pid,"num_threads":pid,"ppid":1}

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


def test_sorting_by_memory_sees_every_process_not_just_top_cpu():
    """Regression: sorting must happen before truncation.

    The memory hog uses no CPU, so a CPU-sorted-then-truncated list would drop
    it entirely and a client-side re-sort would never see it. Sorting by memory
    must return it first.
    """
    procs = [FakeProc(i, "busy%d" % i, 90.0 - i, 0.1, 1000) for i in range(20)]
    procs.append(FakeProc(999, "memhog", 0.0, 80.0, 9 * 1024 ** 3))
    with patch("sysview.processes.psutil.process_iter", return_value=iter(procs)):
        r = collect_processes(limit=5, sort="memory_percent")
    assert r["processes"][0]["name"] == "memhog"
    assert r["total"] == 21


def test_sort_ascending():
    procs = [FakeProc(1, "a", 5.0, 0.1, 100), FakeProc(2, "b", 1.0, 0.1, 100)]
    with patch("sysview.processes.psutil.process_iter", return_value=iter(procs)):
        r = collect_processes(sort="cpu_percent", desc=False)
    assert [p["name"] for p in r["processes"]] == ["b", "a"]


def test_sort_by_name_is_case_insensitive():
    procs = [FakeProc(1, "Zebra", 1.0, 0.1, 100), FakeProc(2, "apple", 1.0, 0.1, 100)]
    with patch("sysview.processes.psutil.process_iter", return_value=iter(procs)):
        r = collect_processes(sort="name", desc=False)
    assert [p["name"] for p in r["processes"]] == ["apple", "Zebra"]


def test_unknown_sort_key_falls_back_to_cpu():
    procs = [FakeProc(1, "slow", 1.0, 0.1, 100), FakeProc(2, "fast", 90.0, 0.1, 100)]
    with patch("sysview.processes.psutil.process_iter", return_value=iter(procs)):
        r = collect_processes(sort="'; DROP TABLE --")
    assert r["sort"] == "cpu_percent"
    assert r["processes"][0]["name"] == "fast"


def test_filter_applies_to_every_process_before_truncation():
    """The needle only matches a low-CPU process ranked far below the cut."""
    procs = [FakeProc(i, "busy%d" % i, 90.0 - i, 0.1, 100) for i in range(30)]
    procs.append(FakeProc(777, "needle", 0.0, 0.1, 100))
    with patch("sysview.processes.psutil.process_iter", return_value=iter(procs)):
        r = collect_processes(limit=5, query="needle")
    assert [p["name"] for p in r["processes"]] == ["needle"]
    assert r["matched"] == 1
    assert r["total"] == 31


def test_filter_matches_pid_prefix_and_cmdline():
    def procs():
        # A fresh iterator per call: process_iter is consumed once per request.
        return iter([FakeProc(1234, "alpha", 1.0, 0.1, 100),
                     FakeProc(9, "beta", 1.0, 0.1, 100)])
    with patch("sysview.processes.psutil.process_iter", side_effect=lambda *a, **k: procs()):
        by_pid = collect_processes(query="123")
        by_cmd = collect_processes(query="--flag")
    assert [p["name"] for p in by_pid["processes"]] == ["alpha"]
    assert by_cmd["matched"] == 2


def test_new_fields_are_present():
    procs = [FakeProc(42, "proc", 1.0, 0.1, 100)]
    with patch("sysview.processes.psutil.process_iter", return_value=iter(procs)):
        e = collect_processes()["processes"][0]
    assert e["started"] == 1042.0
    assert e["threads"] == 42
    assert e["ppid"] == 1
