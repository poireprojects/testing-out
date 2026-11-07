"""Release gate: does anything that can change the desktop app differ from the last published build?

    python packaging/app_changed.py            # prints app=true|false (and appends it to $GITHUB_OUTPUT)

Used by .github/workflows/build.yml. The test suite runs on every push; the installers and the "latest"
release are built only when this prints app=true. Research-only commits (research results, research scripts,
tests, docs, and the research modules the app never imports) no longer publish a new installer.

Rules:
* the base is the commit of the newest published build-N tag (merge-base with HEAD), so an app change is
  never missed even if an intermediate run was cancelled or skipped;
* a path is app-relevant unless it matches NON_APP below: unknown or new paths count as app changes;
* no build tag, a git error, a version tag (v*) or a manual run (workflow_dispatch) means app=true.
To force a rebuild after a workflow-only change, run the workflow manually.
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys

NON_APP = [
    "research/*", "scripts/*", "tests/*", "docs/*", "screenshots/*", ".github/*",
    "autotrader/*",          # phase-5 research prototype: outside dkapp, never imported by the app or bundled
    "README.md", "requirements-research.txt", ".gitignore", "packaging/app_changed.py",
    # dkapp research modules that are bundled but never imported by the app (tests/test_release_gate.py
    # checks this against the app's import graph, so this list can't silently go stale)
    "dkapp/research/academic.py", "dkapp/research/data.py", "dkapp/research/dataset.py",
    "dkapp/research/diagnose.py", "dkapp/research/economics.py", "dkapp/research/evaluate.py",
    "dkapp/research/events.py", "dkapp/research/ledger.py", "dkapp/research/phase2.py",
    "dkapp/research/portfolio.py",
]


def is_app_path(path: str) -> bool:
    return not any(fnmatch.fnmatchcase(path, pat) for pat in NON_APP)


def app_changed(paths: list[str]) -> bool:
    return any(is_app_path(p) for p in paths)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def last_build_base() -> str | None:
    tags = [t for t in _git("tag", "-l", "build-*").split() if re.fullmatch(r"build-\d+", t)]
    if not tags:
        return None
    newest = max(tags, key=lambda t: int(t.split("-")[1]))
    return _git("merge-base", f"{newest}^{{commit}}", "HEAD")


def decide() -> tuple[bool, str]:
    event, ref = os.environ.get("GITHUB_EVENT_NAME", ""), os.environ.get("GITHUB_REF", "")
    if event == "workflow_dispatch":
        return True, "manual run"
    if ref.startswith("refs/tags/"):
        return True, "version tag"
    try:
        base = last_build_base()
        if base is None:
            return True, "no published build to compare with"
        changed = [p for p in _git("diff", "--name-only", base, "HEAD").splitlines() if p]
    except subprocess.CalledProcessError as exc:
        return True, f"git failed ({exc.stderr.strip()[:200]})"
    app = [p for p in changed if is_app_path(p)]
    if app:
        return True, f"{len(app)} app file(s) changed since {base[:7]}: " + ", ".join(app[:10])
    return False, f"only research/test/doc files changed since {base[:7]} ({len(changed)} file(s))"


if __name__ == "__main__":
    ok, why = decide()
    print(f"app={'true' if ok else 'false'}  # {why}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"app={'true' if ok else 'false'}\n")
    sys.exit(0)
