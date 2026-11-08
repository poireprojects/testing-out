"""Release notes and version numbers (CHANGELOG.md + packaging/release_notes.py + .github/workflows/build.yml):
every published build has notes, the version is the same everywhere, and an app change can only be published
with a new version and its notes."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))

import release_notes as rn  # noqa: E402

from stockpilot import __version__, legacy  # noqa: E402

# every build published on GitHub, from the first one to the rename
PUBLISHED = [8, 9, 13, 15, 17, 20, 21, 22, 23, 27, 30, 31, 32, 38, 43, 47, 48, 57]


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def test_the_version_is_the_same_everywhere():
    v = __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", v)
    assert rn.current_version() == v
    assert f'version = "{v}"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'VERSION = "{v}"' in (ROOT / "packaging" / "stockpilot.spec").read_text(encoding="utf-8")
    assert f'#define MyAppVersion "{v}"' in (ROOT / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
    info = (ROOT / "packaging" / "windows" / "version_info.txt").read_text(encoding="utf-8")
    four = "(" + ", ".join(v.split(".") + ["0"]) + ")"
    assert info.count(four) == 2 and info.count(f"'{v}'") == 2


def test_the_changelog_starts_with_this_version_and_lists_versions_newest_first():
    secs = rn.parse()
    assert secs[0].version == __version__
    assert rn.problems(__version__, secs, previous=None) == []
    assert [s.version for s in secs] == sorted((s.version for s in secs), key=rn.vkey, reverse=True)


@pytest.mark.parametrize("build", PUBLISHED)
def test_every_published_build_has_its_own_notes(build):
    secs = rn.parse()
    text = rn.old_notes(build, secs)
    assert len(text) > 120 and "latest release" in text
    expected = f"StockPilot 1.1.0 (build {build})" if build == 57 else f"Build {build} (1.0.0)"
    assert rn.old_title(build, secs) == expected


def test_titles():
    secs = rn.parse()
    assert rn.title(58, "1.2.0") == "StockPilot 1.2.0 (build 58)"
    assert rn.old_title(57, secs) == "StockPilot 1.1.0 (build 57)"
    assert rn.old_title(48, secs) == "Build 48 (1.0.0)"
    assert set(PUBLISHED) <= set(rn.old_builds(10_000, secs))


def test_release_notes_put_this_version_first_then_every_earlier_one():
    secs = rn.parse()
    text = rn.notes(58, "0123456789abcdef", secs)
    assert text.startswith(f"**StockPilot {__version__}** · build 58 · commit `0123456`")
    first, _, rest = text.partition("<details>")
    assert f"## What's new in {__version__}" in first
    for s in secs[1:]:
        assert f"## {s.heading}" in rest and s.heading not in first
    assert "</details>" in rest and "CHANGELOG.md" in text


def test_no_notes_carry_the_previous_name():
    secs = rn.parse()
    texts = [(ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), rn.notes(58, "0" * 40, secs)]
    texts += [rn.old_notes(b, secs) + rn.old_title(b, secs) for b in PUBLISHED]
    for t in texts:
        assert legacy.PREVIOUS_APP_ID not in _letters(t)


def test_an_app_change_needs_a_new_version_and_notes():
    secs = rn.parse("## 1.3.0 · 2026-12-01\n- x\n\n## 1.2.0 · 2026-11-08\n- y\n")
    assert rn.problems("1.3.0", secs, previous="1.2.0") == []
    assert any("raise the version" in p for p in rn.problems("1.2.0", rn.parse("## 1.2.0 · d\n- y\n"), "1.2.0"))
    assert rn.problems("1.2.0", rn.parse("## 1.2.0 · d\n- y\n"), "1.2.0", manual=True) == []   # manual rebuild
    assert any("no section" in p for p in rn.problems("1.4.0", secs, previous="1.3.0"))
    assert any("lower" in p for p in rn.problems("1.3.0", secs, previous="2.0.0"))
    assert any("newest first" in p for p in rn.problems("1.3.0", rn.parse("## 1.2.0 · a\n\n## 1.3.0 · b\n"), None))


def test_builds_are_found_in_subsections_and_single_build_headings():
    secs = rn.parse("## 2.0.0 · build 70 · d\n- a\n\n## 1.0.0 · builds 1–3 · d\nintro\n\n### Build 3 · d\n- c\n\n"
                    "### Build 1 · d\n- b\n")
    assert secs[0].builds == {70: "- a"}
    assert secs[1].builds == {3: "- c", 1: "- b"}


def test_the_workflow_uses_the_notes_and_checks_the_version():
    yaml = pytest.importorskip("yaml")
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8"))["jobs"]
    check = jobs["changes"]["steps"][2]
    assert "release_notes.py check" in check["run"] and "steps.gate.outputs.app == 'true'" in check["if"]
    steps = jobs["latest"]["steps"]
    assert steps[0]["uses"].startswith("actions/checkout")
    publish = next(s for s in steps if s.get("name") == "Publish the latest build")["run"]
    assert "release_notes.py notes" in publish and "--notes-file notes.md" in publish
    assert "release_notes.py title" in publish and "--latest" in publish
    old = next(s for s in steps if s.get("name") == "Notes for the earlier releases")["run"]
    assert "--latest=false" in old and "old-notes" in old and "|| echo" in old   # never moves "latest", never fails


def test_the_checker_runs_on_the_standard_library_only():
    src = (ROOT / "packaging" / "release_notes.py").read_text(encoding="utf-8")
    imports = set(re.findall(r"^(?:import|from) (\w+)", src, re.M))
    assert imports <= {"__future__", "os", "re", "subprocess", "sys", "dataclasses", "pathlib"}


def test_hard_wrapped_lines_are_joined_for_github():
    assert rn.unwrap("- one\n  two\n- three\n\nsome\ntext\n\n### Head\n- x") == \
        "- one two\n- three\n\nsome text\n\n### Head\n- x"
