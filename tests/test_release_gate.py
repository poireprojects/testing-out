"""The release gate (packaging/app_changed.py + .github/workflows/build.yml): research-only commits run the tests
but never publish a desktop installer; anything that can change the app still does."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))

import app_changed as gate  # noqa: E402


@pytest.mark.parametrize("path", [
    "research/runs/2026-11-07-phase4/corrected/results_corrected.json", "research/ledger.json",
    "scripts/phase4_corrected.py", "tests/test_events.py", "docs/results/x.md", "README.md",
    ".github/workflows/build.yml", "stockpilot/research/events.py", "stockpilot/research/phase2.py",
    "requirements-research.txt", "packaging/app_changed.py", "autotrader/runner.py", "autotrader/owner_policy.json",
])
def test_research_test_and_doc_paths_do_not_release(path):
    assert not gate.is_app_path(path)


@pytest.mark.parametrize("path", [
    "stockpilot/ui/qml/Main.qml", "stockpilot/engine/trading_engine.py", "stockpilot/risk/fees.py",
    "stockpilot/research/forward.py", "stockpilot/research/daily_shadow.py", "stockpilot/research/__init__.py",
    "packaging/stockpilot.spec", "packaging/windows/installer.iss", "requirements.txt", "requirements-dev.txt",
    "pyproject.toml", "some_new_top_level_file.py",
])
def test_app_paths_and_unknown_paths_do_release(path):
    assert gate.is_app_path(path)


def _app_reachable_modules() -> set[str]:
    """stockpilot modules reachable by static import from the packaged app's entry points."""
    mods = {}
    for p in (ROOT / "stockpilot").rglob("*.py"):
        name = ".".join(p.relative_to(ROOT).with_suffix("").parts)
        mods[name.removesuffix(".__init__")] = p

    def deps(name, p):
        pkg = name if p.name == "__init__.py" else name.rsplit(".", 1)[0]
        out = set()
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Import):
                out |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                base = n.module or ""
                if n.level:
                    parts = pkg.split(".")[: len(pkg.split(".")) - (n.level - 1)]
                    base = ".".join(parts + ([base] if base else []))
                out |= {base} | {f"{base}.{a.name}" for a in n.names}
        return {d for d in out if d in mods}

    entry = {"stockpilot", "stockpilot.__main__", "stockpilot.ui.app", "stockpilot.ui.charts", "stockpilot.ui.controller", "stockpilot.ui.models"}
    seen, stack = set(), list(entry)
    while stack:
        m = stack.pop()
        if m in seen:
            continue
        seen.add(m)
        parts = m.split(".")
        stack += [".".join(parts[:i]) for i in range(1, len(parts)) if ".".join(parts[:i]) in mods]
        stack += deps(m, mods[m])
    return seen


def test_research_modules_listed_as_non_app_are_never_imported_by_the_app():
    reach = _app_reachable_modules()
    assert "stockpilot.research.forward" in reach                        # sanity: the graph sees the app's research use
    listed = [p for p in gate.NON_APP if p.startswith("stockpilot/")]
    assert listed
    for p in listed:
        mod = p.removesuffix(".py").replace("/", ".")
        assert mod not in reach, f"{mod} is imported by the app: remove {p} from NON_APP"


def test_the_app_never_imports_the_research_prototype():
    for p in (ROOT / "stockpilot").rglob("*.py"):
        assert "autotrader" not in p.read_text(encoding="utf-8"), p
    spec = (ROOT / "packaging" / "stockpilot.spec").read_text(encoding="utf-8")
    assert "autotrader" not in spec and 'collect_submodules("stockpilot")' in spec


def test_launcher_entry_point_is_the_one_the_graph_starts_from():
    assert "from stockpilot.ui.app import main" in (ROOT / "packaging" / "launch.py").read_text(encoding="utf-8")


def test_every_installer_and_release_job_is_gated_and_the_tests_are_not():
    yaml = pytest.importorskip("yaml")
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8"))["jobs"]
    assert "if" not in jobs["test"] and "if" not in jobs["changes"]
    for name in ("windows", "windows-uninstall-test", "upgrade-from-previous", "macos", "latest", "latest-mac"):
        assert "needs.changes.outputs.app == 'true'" in jobs[name]["if"], name
        needs = jobs[name]["needs"]
        assert "changes" in (needs if isinstance(needs, list) else [needs]), name
    steps = jobs["changes"]["steps"]
    assert steps[0]["with"]["fetch-depth"] == 0                      # tags and history for the comparison
    assert "packaging/app_changed.py" in steps[1]["run"]


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo, path, text="x"):
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", path)


def test_decision_compares_against_the_newest_published_build(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/claude/x")
    _commit(repo, "stockpilot/ui/app.py")
    assert gate.decide()[0] is True                                   # nothing published yet
    _git(repo, "tag", "build-9")
    _commit(repo, "stockpilot/ui/app.py", "y")
    _git(repo, "tag", "build-10")                                     # numeric, not lexical, ordering
    _commit(repo, "research/runs/a.json")
    _commit(repo, "scripts/a.py")
    _commit(repo, "stockpilot/research/events.py")
    assert gate.decide()[0] is False                                  # research only since build-10
    _commit(repo, "stockpilot/risk/fees.py")
    _commit(repo, "research/runs/b.json")
    ok, why = gate.decide()
    assert ok and "stockpilot/risk/fees.py" in why                         # an app change is never hidden by a later commit
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    assert gate.decide() == (True, "manual run")
