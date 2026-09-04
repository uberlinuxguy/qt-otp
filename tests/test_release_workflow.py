"""Guard the release pipeline.

Nobody looks at the workflow until a tag is pushed, which is the worst moment to
discover a renamed file or a broken trigger.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="pyyaml is part of the dev extra")

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
SPEC = REPO / "qt-otp.spec"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def job(workflow: dict) -> dict:
    return workflow["jobs"]["windows-exe"]


def triggers(workflow: dict) -> dict:
    # PyYAML reads the bare `on:` key as the boolean True.
    return workflow.get("on", workflow.get(True))


def test_the_workflow_and_spec_exist() -> None:
    assert WORKFLOW.is_file()
    assert SPEC.is_file()


def test_it_triggers_on_vx_y_z_tags(workflow: dict) -> None:
    tags = triggers(workflow)["push"]["tags"]
    assert "v[0-9]+.[0-9]+.[0-9]+" in tags, f"vX.Y.Z tags would not build: {tags}"


def test_the_tag_check_compares_the_whole_version(job: dict) -> None:
    """The trigger shape and the tag check have to agree.

    The check used to expect "v" plus the *first two* parts of __version__,
    which stopped matching the moment the trigger moved to vX.Y.Z: every tagged
    build would then fail against a tag the step had derived itself.
    """
    step = next(s for s in job["steps"] if "tag matches" in s.get("name", ""))
    assert '"v$version"' in step["run"], "the tag must be compared against the full version"


def test_the_packaged_version_can_be_tagged() -> None:
    """A two-part __version__ would be untaggable, and only a tag would say so."""
    version = (REPO / "otpvault" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', version, re.MULTILINE)
    assert match, "__version__ is no longer where the spec and workflow look for it"
    assert re.fullmatch(r"\d+\.\d+\.\d+", match.group(1)), (
        f"__version__ is {match.group(1)!r}; release tags are vX.Y.Z, so it needs three parts"
    )


def test_it_can_write_releases(workflow: dict) -> None:
    assert workflow["permissions"]["contents"] == "write"


def test_it_builds_on_windows(job: dict) -> None:
    assert job["runs-on"].startswith("windows")


def test_the_build_runs_the_tests_before_releasing(job: dict) -> None:
    names = [step.get("name", "") for step in job["steps"]]
    assert any("test suite" in name for name in names)
    test_index = next(i for i, n in enumerate(names) if "test suite" in n)
    release_index = next(i for i, n in enumerate(names) if "Publish" in n)
    assert test_index < release_index, "the release must not run ahead of the tests"


def test_the_built_executable_is_smoke_tested(job: dict) -> None:
    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "--selftest" in scripts, "a build that cannot start would still be released"


def test_the_release_is_only_published_for_tags(job: dict) -> None:
    publish = next(s for s in job["steps"] if s.get("name", "").startswith("Publish"))
    assert "refs/tags/" in publish["if"]


def test_every_file_the_workflow_runs_exists(job: dict) -> None:
    """Catches a renamed script or spec before a tag does."""
    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    for referenced in ("tools/make_icon.py", "qt-otp.spec"):
        assert referenced in scripts, f"{referenced} is no longer used by the workflow"
        assert (REPO / referenced).is_file(), f"{referenced} is referenced but missing"


def test_the_spec_points_at_files_that_exist() -> None:
    text = SPEC.read_text(encoding="utf-8")
    assert "tools" in text and "entrypoint.py" in text
    assert (REPO / "tools" / "entrypoint.py").is_file()
    for resource in ("qt-otp-icon.svg", "qt-otp-about.svg"):
        assert resource in text, f"{resource} is no longer bundled"
        assert (REPO / "otpvault" / "resources" / resource).is_file()


def test_the_spec_builds_a_windowed_single_file() -> None:
    text = SPEC.read_text(encoding="utf-8")
    assert "console=False" in text, "a GUI app should not open a console window"
    assert "COLLECT" not in text, "onefile means no COLLECT stage"


def test_the_build_extra_provides_pyinstaller() -> None:
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "pyinstaller" in pyproject
    scripts = WORKFLOW.read_text(encoding="utf-8")
    assert '.[dev,build]' in scripts, "the workflow must install the extra that supplies PyInstaller"


def smoke_step(job: dict) -> dict:
    return next(s for s in job["steps"] if s.get("name", "").startswith("Smoke-test"))


def test_the_smoke_test_waits_for_the_gui_executable(job: dict) -> None:
    r"""PowerShell does not block on GUI-subsystem executables.

    A bare `& .\dist\qt-otp.exe` returns before the process has started, so
    $LASTEXITCODE reflects the previous command: every build looks broken, or
    worse, a broken one looks fine. Start-Process -Wait is what actually blocks.
    """
    run = smoke_step(job)["run"]
    assert "Start-Process" in run and "-Wait" in run, "the smoke test would not wait for the exe"
    assert "-PassThru" in run, "without -PassThru there is no exit code to check"
    # The explanatory comment may mention it; the code may not.
    code_lines = [line for line in run.splitlines() if not line.strip().startswith("#")]
    assert "$LASTEXITCODE" not in "\n".join(code_lines), (
        "$LASTEXITCODE is meaningless for a GUI exe here"
    )


def test_the_smoke_test_checks_both_the_exit_code_and_the_report(job: dict) -> None:
    run = smoke_step(job)["run"]
    assert "$proc.ExitCode" in run
    assert "$report.ok" in run, "a report full of failures would otherwise pass silently"
