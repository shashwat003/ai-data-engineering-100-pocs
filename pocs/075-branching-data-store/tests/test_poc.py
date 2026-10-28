import pytest
from branching_data_store.main import run
from branching_data_store.repo import DataRepo, HookRejected, MergeConflict, RepoError, clean_path


@pytest.fixture()
def repo(tmp_path):
    r = DataRepo(tmp_path / "r")
    r.commit("main", {"a.csv": b"1", "b.csv": b"2"}, "seed")
    return r


def test_commit_read_and_atomic_failure(repo):
    assert repo.read("main", "a.csv") == b"1"
    before = repo.head("main").id
    with pytest.raises(RepoError):
        repo.commit("main", {"c.csv": b"3", "missing.csv": None}, "mixed")
    assert (
        repo.head("main").id == before and "c.csv" not in repo.head("main").tree
    )  # all-or-nothing
    with pytest.raises(RepoError):
        repo.commit("main", {}, "empty")


@pytest.mark.parametrize("p", ["../x", "/etc/passwd", "a/../../b", "", "dir/", "a\\b", "x\x00y"])
def test_unsafe_paths_rejected(repo, p):
    with pytest.raises(RepoError):
        clean_path(p)
    with pytest.raises(RepoError):
        repo.commit("main", {p: b"x"}, "evil")


def test_branch_isolation_and_names(repo):
    repo.create_branch("dev")
    repo.commit("dev", {"a.csv": b"changed"}, "edit")
    assert repo.read("main", "a.csv") == b"1" and repo.read("dev", "a.csv") == b"changed"
    assert repo.diff("main", "dev") == {"a.csv": "modified"}
    for bad in ("dev", "../x", "-x", "a..b", ""):
        with pytest.raises(RepoError):
            repo.create_branch(bad)
    with pytest.raises(RepoError):
        repo.delete_branch("main")


def test_merge_cases(repo):
    for n in ("x", "y"):
        repo.create_branch(n)
    repo.commit("x", {"a.csv": b"X", "new.csv": b"n"}, "x")
    repo.commit("y", {"b.csv": None}, "y deletes b")
    repo.merge("x", "main")  # fast-forward
    m = repo.merge("y", "main")  # real 3-way merge
    assert (
        len(m.parents) == 2
        and repo.read("main", "a.csv") == b"X"
        and "b.csv" not in m.tree
        and "new.csv" in m.tree
    )
    assert repo.merge("x", "main").id == m.id  # already merged: no-op


def test_conflict_and_delete_vs_modify(repo):
    repo.create_branch("p")
    repo.create_branch("q")
    repo.commit("p", {"a.csv": b"P"}, "p")
    repo.commit("q", {"a.csv": None}, "q deletes")
    repo.merge("p", "main")
    with pytest.raises(MergeConflict) as e:
        repo.merge("q", "main")
    assert e.value.paths == ["a.csv"] and repo.read("main", "a.csv") == b"P"


def test_same_change_on_both_sides_is_not_a_conflict(repo):
    repo.create_branch("p")
    repo.commit("p", {"a.csv": b"same"}, "p")
    repo.commit("main", {"a.csv": b"same"}, "m")
    assert repo.read(repo.merge("p", "main").id, "a.csv") == b"same"


def test_hook_veto_leaves_dst_untouched(repo):
    repo.create_branch("d")
    repo.commit("d", {"a.csv": b"bad"}, "d")
    head = repo.head("main").id

    def hook(files):
        if files["a.csv"] == b"bad":
            raise ValueError("no")

    with pytest.raises(HookRejected):
        repo.merge("d", "main", pre_merge=hook)
    assert repo.head("main").id == head


def test_revert_and_its_conflict(repo):
    c1 = repo.commit("main", {"a.csv": b"v2"}, "v2")
    repo.commit("main", {"c.csv": b"later"}, "later")
    r = repo.revert("main", c1.id)
    assert repo.read(r.id, "a.csv") == b"1" and repo.read(r.id, "c.csv") == b"later"
    c3 = repo.commit("main", {"a.csv": b"v3"}, "v3")
    c4 = repo.commit("main", {"a.csv": b"v4"}, "v4")
    with pytest.raises(MergeConflict):
        repo.revert("main", c3.id)
    assert c4


def test_dedup_gc_and_tamper(repo):
    repo.commit("main", {"copy.csv": b"1"}, "same content as a.csv")
    assert len(list((repo.root / "objects").iterdir())) == 2
    repo.create_branch("s")
    repo.commit("s", {"u.bin": b"unique"}, "u")
    repo.delete_branch("s")
    assert repo.gc() == 1 and repo.verify() == []
    next(iter((repo.root / "objects").iterdir())).write_bytes(b"corrupt")
    assert repo.verify()


def test_unknown_refs(repo):
    with pytest.raises(RepoError):
        repo.head("nope")
    with pytest.raises(RepoError):
        repo.read("0" * 64, "a.csv")
    with pytest.raises(RepoError):
        repo.read("main", "zzz")


def test_history_survives_reopen(tmp_path):
    r = DataRepo(tmp_path / "r")
    r.commit("main", {"a": b"1"}, "m")
    assert DataRepo(tmp_path / "r").read("main", "a") == b"1"


def test_demo_claims():
    r = run()
    assert (
        r["bad_run_merged"] is False and r["main_unchanged_after_rejection"] and r["fast_forward"]
    )
    assert (
        r["conflict_detected"]
        and r["revert_restores_previous"]
        and r["later_work_kept_after_revert"]
    )
    assert r["tamper_detected"] and r["verify_clean"] and r["gc_removed_unreachable"] == 1
