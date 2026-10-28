import json
from pathlib import Path

import pytest
from poc_portfolio_site.cli import main as cli
from poc_portfolio_site.model import check_repo, load_repo, parse_readme
from poc_portfolio_site.site import build_site

GOOD = """# POC 001: Demo

## Problem
x

## Approach
```mermaid
flowchart LR
  A --> B
```

## Run it
make demo

## Evidence status
| Claim | Status |
|---|---|
| core | Tested (real) |
| llm | SIMULATED |
| gpu | NOT RUN |

## Results
r

## Limitations
- one
- two
"""


def make_repo(
    tmp_path: Path,
    readme=GOOD,
    evidence="tests: 3 pass",
    tests=True,
    results=True,
    title="Demo <b>x</b>",
) -> Path:
    (tmp_path / "catalogue").mkdir(parents=True)
    (tmp_path / "catalogue" / "pocs.csv").write_text(
        f'id,slug,title,category,stack,runs_local,wave,evidence\n1,demo,"{title}",Cat,"Py, Z",yes,1,{evidence}\n2,later,Planned,Cat,Py,yes,2,\n'
    )
    d = tmp_path / "pocs" / "001-demo"
    (d / "tests").mkdir(parents=True)
    (d / "README.md").write_text(readme)
    if tests:
        (d / "tests" / "test_a.py").write_text(
            "def test_a():\n    pass\n\nasync def test_b():\n    pass\n"
        )
    if results:
        (d / "results").mkdir()
        (d / "results" / "results.md").write_text("ok")
    (d / ".heavy-tests").touch()
    return tmp_path


def test_parse_readme_sections_labels_limitations():
    i = parse_readme(GOOD)
    assert len(i["sections"]) == 6 and i["mermaid"] and i["limitations"] == 2
    assert i["labels"] == {"tested", "simulated", "unverified"}
    assert "Approach" in parse_readme("## Architecture\nx")["sections"]  # alias
    assert parse_readme("")["limitations"] == 0


def test_load_and_maturity(tmp_path):
    p1, p2 = load_repo(make_repo(tmp_path))
    assert (
        p1.maturity == 3
        and p1.n_tests == 2
        and ".heavy-tests" in p1.markers
        and p2.maturity == 0
        and not p2.built
    )
    assert check_repo([p1, p2]) == []


@pytest.mark.parametrize(
    "kwargs,needle,level",
    [
        ({"tests": False}, "no tests", 1),
        ({"results": False}, "results", 2),
        ({"readme": GOOD.replace("```mermaid", "```text")}, "Mermaid", 2),
        ({"readme": GOOD.replace("## Limitations\n- one\n- two\n", "")}, "Limitations", 2),
    ],
)
def test_check_flags_each_defect(tmp_path, kwargs, needle, level):
    (p1, _) = load_repo(make_repo(tmp_path, **kwargs))
    assert any(needle in m for m in check_repo([p1])) and p1.maturity == level


def test_built_without_directory_is_flagged(tmp_path):
    root = make_repo(tmp_path)
    (root / "catalogue" / "pocs.csv").write_text(
        (root / "catalogue" / "pocs.csv")
        .read_text()
        .replace("2,later,Planned,Cat,Py,yes,2,", "2,later,Planned,Cat,Py,yes,2,claimed done")
    )
    assert any("no directory" in m for m in check_repo(load_repo(root)))


def test_site_escapes_everything(tmp_path):
    evil = GOOD.replace(
        "x\n\n## Approach", "<script>alert(1)</script> [x](javascript:alert(2))\n\n## Approach", 1
    )
    root = make_repo(
        tmp_path / "r", readme=evil, title="Evil <img src=x onerror=alert(1)> </script>"
    )
    out = tmp_path / "site"
    s = build_site(load_repo(root), out, flagships={1})
    assert s["built"] == 1 and s["total"] == 2
    page = (out / "poc" / "001.html").read_text()
    index = (out / "index.html").read_text()
    assert (
        "<script>alert(1)" not in page
        and "<img src=x" not in page
        and 'href="javascript' not in page.lower()
        and "<a " not in page.split("<h2>Problem")[1].split("<h2>")[0]
    )
    assert "<\\/script>" in index and index.count("</script>") == 2  # data block + code block only
    data = json.loads((out / "search-index.json").read_text())
    assert (
        data[0]["flagship"]
        and data[0]["maturity"] == "verified"
        and data[1]["maturity"] == "planned"
    )
    assert not (out / "poc" / "002.html").exists()


def test_no_external_requests_in_site(tmp_path):
    out = tmp_path / "s"
    build_site(load_repo(make_repo(tmp_path / "r")), out)
    for f in out.rglob("*.html"):
        t = f.read_text()
        assert (
            "http://" not in t
            and "https://" not in t
            and "src=" not in t.replace("placeholder", "")
        )


def test_cli_check_build_stats_and_errors(tmp_path, capsys):
    root = make_repo(tmp_path / "r")
    assert cli(["--root", str(root), "check"]) == 0
    assert (
        cli(["--root", str(root), "build", "--out", str(tmp_path / "o"), "--flagships", "1"]) == 0
        and (tmp_path / "o" / "index.html").exists()
    )
    assert cli(["--root", str(root), "stats"]) == 0 and "verified 1" in capsys.readouterr().out
    assert cli(["--root", str(tmp_path / "nowhere"), "check"]) == 2
    bad = make_repo(tmp_path / "r2", tests=False)
    assert cli(["--root", str(bad), "check"]) == 1


def test_real_repository_has_no_consistency_problems():
    from poc_portfolio_site.main import REPO, run

    r = run(Path(__file__).resolve().parent / "_site_tmp")
    import shutil

    shutil.rmtree(Path(__file__).resolve().parent / "_site_tmp")
    assert r["check_problems"] == [], r["check_problems"]
    assert r["summary"]["total"] == 100 and (REPO / "catalogue" / "pocs.csv").exists()
