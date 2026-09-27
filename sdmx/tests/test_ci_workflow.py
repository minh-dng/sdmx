"""Exercise the CI cache recovery with a local Git remote, without network access."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from sdmx.testing import data

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/pytest.yaml"


def git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


@pytest.mark.parametrize("state", ["missing", "clean", "deleted", "modified"])
def test_ci_test_data_cache_recovery(tmp_path, monkeypatch, state):
    source = tmp_path / "source"
    source.mkdir()
    git("init", "-b", "main", cwd=source)
    (source / "specimen.xml").write_text("original")
    git("add", "specimen.xml", cwd=source)
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "Initial",
        cwd=source,
    )
    remote = tmp_path / "remote.git"
    git("clone", "--bare", str(source), str(remote), cwd=tmp_path)

    cache = tmp_path / "cache"
    cache.mkdir()
    schema = cache / "schemas.zip"
    schema.write_bytes(b"schema archive")
    checkout = cache / "test-data"
    monkeypatch.setattr(data, "REMOTE_URL", str(remote))
    if state != "missing":
        data.SpecimenCollection(checkout, fetch=True)
        if state == "deleted":
            (checkout / "specimen.xml").unlink()
        elif state == "modified":
            (checkout / "specimen.xml").write_text("changed")

    # Run the exact bash block from the workflow; only replace the Actions output
    # expression and the uv command (which invokes the real fetch with a local remote).
    workflow = WORKFLOW.read_text()
    block = workflow.split("    - name: Run pytest\n      run: |\n", 1)[1].split(
        "      shell: bash", 1
    )[0]
    script = textwrap.dedent(block).replace(
        "${{ steps.sdmx-cache.outputs.dir }}", str(cache)
    )
    runner = """\
uv() {
  "$PYTHON" -c 'import os, sys; from pathlib import Path; from sdmx.testing import data; data.REMOTE_URL = os.environ["REMOTE"]; data.SpecimenCollection(Path(os.environ["CHECKOUT"]), "--sdmx-fetch-data" in sys.argv); Path(os.environ["FETCH_FLAG"]).write_text(str("--sdmx-fetch-data" in sys.argv))' "$@"
}
"""
    flag = tmp_path / "fetch-flag"
    subprocess.run(
        ["bash", "-e", "-c", runner + script],
        check=True,
        env={
            **os.environ,
            "PYTHON": sys.executable,
            "PYTHONPATH": str(WORKFLOW.parents[2]),
            "REMOTE": str(remote),
            "CHECKOUT": str(checkout),
            "FETCH_FLAG": str(flag),
        },
    )
    assert flag.read_text() == str(state != "clean")
    assert (checkout / "specimen.xml").read_text() == "original"
    assert schema.read_bytes() == b"schema archive"
