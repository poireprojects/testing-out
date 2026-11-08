"""Release titles and notes from CHANGELOG.md, and the version check that goes with them.

    python3 packaging/release_notes.py check                  # fails unless this app change has a new version + notes
    python3 packaging/release_notes.py title BUILD            # "StockPilot 1.2.0 (build 58)"
    python3 packaging/release_notes.py notes BUILD SHA        # notes for the release being published
    python3 packaging/release_notes.py old-builds BUILD       # earlier build numbers that have notes, one per line
    python3 packaging/release_notes.py old-title N            # title for an earlier release
    python3 packaging/release_notes.py old-notes N            # notes for an earlier release

Used by .github/workflows/build.yml (standard library only, so it runs before anything is installed).

CHANGELOG.md layout: one "## X.Y.Z · ..." section per version, newest first. A version published as a single
build says so in its heading ("## 1.1.0 · build 57 · ..."); a version spread over several builds has a
"### Build N · ..." subsection for each.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_FILE = ROOT / "stockpilot" / "__init__.py"
APP = "StockPilot"
_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.M)


@dataclass
class Section:
    version: str
    heading: str                  # the heading line without "## "
    body: str                     # everything under the heading, up to the next version
    builds: dict[int, str] = field(default_factory=dict)   # build number -> its notes


def vkey(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def current_version(text: str | None = None) -> str:
    m = _VERSION_RE.search(text if text is not None else VERSION_FILE.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("no __version__ in stockpilot/__init__.py")
    return m.group(1)


def parse(text: str | None = None) -> list[Section]:
    text = text if text is not None else CHANGELOG.read_text(encoding="utf-8")
    out: list[Section] = []
    for chunk in re.split(r"^## ", text, flags=re.M)[1:]:
        heading, _, body = chunk.partition("\n")
        m = re.match(r"(\d+\.\d+\.\d+)\b", heading)
        if not m:
            continue
        sec = Section(m.group(1), heading.strip(), body.strip())
        subs = re.split(r"^### Build (\d+)\b[^\n]*\n", body, flags=re.M)
        for i in range(1, len(subs) - 1, 2):
            sec.builds[int(subs[i])] = subs[i + 1].strip()
        single = re.search(r"\bbuild (\d+)\b", heading)
        if single and not sec.builds:
            sec.builds[int(single.group(1))] = sec.body
        out.append(sec)
    return out


def problems(version: str, sections: list[Section], previous: str | None, manual: bool = False) -> list[str]:
    """What stops this app change from being published (empty: fine)."""
    found = []
    versions = [s.version for s in sections]
    if version not in versions:
        found.append(f"CHANGELOG.md has no section for version {version}: add '## {version} · <date>' with the notes")
    elif versions[0] != version:
        found.append(f"the newest CHANGELOG.md section is {versions[0]}, but the app is version {version}")
    if versions != sorted(versions, key=vkey, reverse=True) or len(set(versions)) != len(versions):
        found.append("CHANGELOG.md versions must be unique and newest first")
    if previous and not manual:
        if vkey(version) == vkey(previous):
            found.append(f"the app changed since the last published build, which is already version {version}: "
                         "raise the version (stockpilot/__init__.py and the packaging files) and add its notes")
        elif vkey(version) < vkey(previous):
            found.append(f"version {version} is lower than the last published version {previous}")
    return found


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def published_version() -> tuple[str | None, str | None]:
    """(newest build-N tag, the app version in it), or (None, None) when nothing is published yet."""
    try:
        tags = [t for t in _git("tag", "-l", "build-*").split() if re.fullmatch(r"build-\d+", t)]
    except Exception:
        return None, None
    if not tags:
        return None, None
    tag = max(tags, key=lambda t: int(t.split("-")[1]))
    for path in ("stockpilot/__init__.py", "dkapp/__init__.py"):   # the package's name before 1.1.0
        try:
            return tag, current_version(_git("show", f"{tag}:{path}"))
        except Exception:
            continue
    return tag, None


def unwrap(text: str) -> str:
    """Join hard-wrapped lines: GitHub shows every newline in release notes as a line break."""
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append("")
        elif s.startswith(("- ", "#", "<", "|")) or not out or not out[-1] or out[-1].lstrip().startswith(("#", "<")):
            out.append(line.rstrip())
        else:
            out[-1] += " " + s
    return "\n".join(out)


def _repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY", "poireprojects/testing-out")


def title(build: int, version: str | None = None) -> str:
    return f"{APP} {version or current_version()} (build {build})"


def notes(build: int, sha: str, sections: list[Section] | None = None, version: str | None = None) -> str:
    sections = sections if sections is not None else parse()
    version = version or current_version()
    cur = next(s for s in sections if s.version == version)
    earlier = [s for s in sections if s.version != version]
    lines = [
        f"**{APP} {version}** · build {build} · commit `{sha[:7]}`",
        "",
        f"**Download:** `{APP}-windows-setup.exe` for Windows 10/11 is below. `{APP}-macos.dmg` for Apple Silicon "
        "Macs is attached as soon as the Mac build finishes. Already have an earlier version? Install this one "
        "over it; nothing is lost.",
        "",
        f"## What's new in {version}",
        "",
        unwrap(cur.body),
    ]
    if earlier:
        lines += ["", "<details>", "<summary>Earlier versions: everything since the first build</summary>", ""]
        for s in earlier:
            lines += [f"## {s.heading}", "", unwrap(s.body), ""]
        lines += ["</details>"]
    lines += ["", f"Full history: [CHANGELOG.md](https://github.com/{_repo()}/blob/{sha}/CHANGELOG.md)"]
    return "\n".join(lines) + "\n"


def _section_of(build: int, sections: list[Section]) -> Section:
    for s in sections:
        if build in s.builds:
            return s
    raise SystemExit(f"CHANGELOG.md has no notes for build {build}")


def old_title(build: int, sections: list[Section] | None = None) -> str:
    s = _section_of(build, sections if sections is not None else parse())
    return f"{APP} {s.version} (build {build})" if vkey(s.version) >= (1, 1, 0) else f"Build {build} ({s.version})"


def old_notes(build: int, sections: list[Section] | None = None) -> str:
    sections = sections if sections is not None else parse()
    s = _section_of(build, sections)
    if vkey(s.version) >= (1, 1, 0):
        head = f"**{APP} {s.version}** · build {build}"
    else:
        head = (f"**Build {build}** · version {s.version} · published before the app was renamed {APP}. "
                f"Installing {APP} upgrades it in place, keeping your data and keys.")
    return "\n".join([
        head, "", unwrap(s.builds[build]), "",
        f"This is an older build. The newest version is always the [latest release](https://github.com/{_repo()}"
        f"/releases/latest); every change is listed in [CHANGELOG.md](https://github.com/{_repo()}/blob/HEAD/"
        "CHANGELOG.md).",
    ]) + "\n"


def old_builds(current_build: int, sections: list[Section] | None = None) -> list[int]:
    sections = sections if sections is not None else parse()
    return sorted({b for s in sections for b in s.builds if b != current_build}, reverse=True)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "check":
        version = current_version()
        tag, previous = published_version()
        manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        found = problems(version, parse(), previous, manual)
        for p in found:
            print(f"::error::{p}")
        print(f"version {version}; last published build: {tag or 'none'} ({previous or 'unknown version'})")
        return 1 if found else 0
    if cmd == "title":
        print(title(int(argv[2])))
    elif cmd == "notes":
        sys.stdout.write(notes(int(argv[2]), argv[3]))
    elif cmd == "old-builds":
        print("\n".join(str(b) for b in old_builds(int(argv[2]))))
    elif cmd == "old-title":
        print(old_title(int(argv[2])))
    elif cmd == "old-notes":
        sys.stdout.write(old_notes(int(argv[2])))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
