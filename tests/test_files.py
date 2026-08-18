# tests/test_files.py
import os
import pytest
from sysview.files import list_directory

@pytest.fixture
def tree(tmp_path):
    real = tmp_path.resolve()
    (real/"adir").mkdir(); (real/"zdir").mkdir()
    (real/"bfile.txt").write_text("hello"); (real/"afile.txt").write_text("hi")
    return real

def test_lists_directories_before_files_alphabetically(tree):
    r=list_directory(str(tree))
    assert [e["name"] for e in r["entries"]]==["adir","zdir","afile.txt","bfile.txt"]
    assert r["error"]==""

def test_entry_metadata(tree):
    by={e["name"]:e for e in list_directory(str(tree))["entries"]}
    assert by["adir"]["is_dir"] is True
    assert by["bfile.txt"]["is_dir"] is False
    assert by["bfile.txt"]["size"]==5
    assert by["bfile.txt"]["mtime"]>0
    assert len(by["bfile.txt"]["mode"])==9

def test_parent_is_none_at_root():
    r=list_directory("/")
    assert r["parent"] is None and r["path"]=="/"

def test_parent_points_one_level_up(tree):
    assert list_directory(str(tree))["parent"]==str(tree.parent)

def test_traversal_is_resolved_not_escaped(tree):
    r=list_directory(str(tree/"adir"/".."))
    assert r["path"]==str(tree) and ".." not in r["path"]

def test_symlink_reports_resolved_target_as_path(tmp_path):
    base=tmp_path.resolve()
    real=base/"real"; real.mkdir(); (real/"inside.txt").write_text("x")
    os.symlink(real, base/"link")
    r=list_directory(str(base/"link"))
    assert r["path"]==str(real)
    assert [e["name"] for e in r["entries"]]==["inside.txt"]

def test_nonexistent_path_returns_error_not_exception():
    r=list_directory("/definitely/does/not/exist/anywhere")
    assert r["entries"]==[] and "not found" in r["error"].lower()

def test_file_path_returns_error(tree):
    r=list_directory(str(tree/"bfile.txt"))
    assert r["entries"]==[] and "not a directory" in r["error"].lower()

def test_unreadable_directory_returns_permission_error(tmp_path):
    if os.geteuid()==0: pytest.skip("root bypasses directory permissions")
    locked=tmp_path/"locked"; locked.mkdir(); os.chmod(locked,0o000)
    try:
        r=list_directory(str(locked))
        assert r["entries"]==[] and "permission denied" in r["error"].lower()
    finally: os.chmod(locked,0o755)

def test_unstattable_entry_is_listed_with_zero_size(tmp_path):
    os.symlink(tmp_path/"missing-target", tmp_path/"broken")
    e=list_directory(str(tmp_path))["entries"][0]
    assert e["name"]=="broken" and e["size"]==0
