"""Static regression checks on the Windows installer's safety logic (packaging/windows/installer.iss).

The installer is exercised for real by packaging/windows/test_uninstall.ps1 on a Windows CI machine
(including failing, hung and missing safety checks); these checks make sure the rules can't silently
regress in the script itself.
"""
import re
from pathlib import Path

ISS = (Path(__file__).resolve().parents[1] / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8-sig")
CODE = ISS[ISS.index("[Code]"):]


def body(name: str) -> str:
    m = re.search(rf"(function|procedure) {name}\b.*?\nend;\n", CODE, re.S)
    assert m, name
    return m.group(0)


def test_restart_manager_is_off():
    assert "CloseApplications=no" in ISS, "Windows Restart Manager would kill the app without the live check"


def test_runapp_never_reports_success_when_the_check_could_not_run():
    f = body("RunApp")
    assert "): Boolean;" in f.splitlines()[0], "RunApp must report whether the program actually ran"
    assert "RC_OK" not in f, "a missing or unstartable program must never be read as 'OK'"
    assert "Result := False" in f and "rc := RC_NOT_RUN" in f


def test_every_unexpected_exit_code_blocks():
    prep = body("PrepareToInstall")
    assert "if rc = RC_OK then exit;" in prep
    assert "Result := Format('Setup can''t continue safely: the app''s safety check failed" in prep
    init = body("InitializeUninstall")
    assert "rc <> RC_OK" in init and "Result := False" in init
    step = body("CurUninstallStepChanged")
    assert "else if rc <> RC_OK then" in step and "RefuseUninstall(Format(" in step
    assert "Abort" in body("RefuseUninstall")


def test_program_that_cannot_run_falls_back_to_independent_checks():
    for name in ("InitializeUninstall", "CurUninstallStepChanged"):
        assert "FallbackCheck()" in body(name), name
    fb = body("FallbackCheck")
    assert "AppProcessRunning(known)" in fb and "AnyLiveDatabase()" in fb
    assert "if not known then" in fb, "an unknown process state must block"
    assert "NothingRunning()" in body("PrepareToInstall")


def test_force_close_only_after_fresh_live_check_and_consent_never_silent():
    may = body("MayForceClose")
    assert "'--check-live'" in may and "rc <> RC_OK" in may
    assert "if Silent then" in may and "MB_DEFBUTTON2" in may, "default answer must be No"
    # ForceClose is only ever called inside an `if MayForceClose(...) then` block
    for name in ("PrepareToInstall", "CurUninstallStepChanged"):
        f = body(name)
        lines = f.splitlines()
        for i, line in enumerate(lines):
            if "ForceClose()" in line and "function" not in line:
                assert any("if MayForceClose(" in prev for prev in lines[max(0, i - 3):i]), (name, line)
        assert " and ForceClose()" not in f, "Pascal Script may not short-circuit: never `MayForceClose and ForceClose`"
    assert "NothingRunning()" in body("ForceClose"), "a force-close must be verified"


def test_uninstall_verifies_nothing_is_running_before_files_go():
    step = body("CurUninstallStepChanged")
    assert step.index("if not NothingRunning() then") < step.index("usPostUninstall")


def test_live_check_covers_test_copies_too():
    assert "profiles" in body("AnyLiveDatabase")


def test_no_code_line_starts_with_a_preprocessor_hash():
    """ISPP reads any line starting with '#' as a directive (that broke the build once)."""
    for n, line in enumerate(CODE.splitlines(), 1):
        assert not line.lstrip().startswith("#"), f"[Code] line {n} starts with '#': {line.strip()[:60]}"
