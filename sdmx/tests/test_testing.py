import json
import shutil
from pathlib import Path

import git
import pytest

from sdmx.testing import data
from sdmx.testing.report import main


def test_report_main(tmp_path):
    # Example input data
    with open(tmp_path.joinpath("TEST.json"), "w") as f:
        json.dump({"TEST": {"foo": "pass"}}, f)

    # Function runs
    main(tmp_path)

    # Output files are generated
    assert tmp_path.joinpath("all-data.json").exists()
    assert tmp_path.joinpath("index.html").exists()


@pytest.fixture
def remote(tmp_path, monkeypatch) -> Path:
    """A local bare repository with one commit, standing in for sdmx-test-data."""
    work = tmp_path / "work"
    with git.Repo.init(work, initial_branch="main") as repo:
        (work / "specimen.xml").write_text("original")
        repo.index.add(["specimen.xml"])
        repo.index.commit("Initial")
        bare = tmp_path / "remote.git"
        repo.clone(bare, bare=True).close()
    monkeypatch.setattr(data, "REMOTE_URL", str(bare))
    return bare


@pytest.mark.parametrize("state", ["missing", "clean", "deleted", "modified"])
def test_specimen_collection_fetch(tmp_path, monkeypatch, remote, state) -> None:
    """:meth:`.SpecimenCollection.fetch` leaves a usable checkout in every state."""
    checkout = tmp_path / "test-data"
    specimen = checkout / "specimen.xml"
    if state != "missing":
        data.SpecimenCollection(checkout, fetch=True)
        if state == "deleted":
            specimen.unlink()
        elif state == "modified":
            specimen.write_text("changed")

        # Repair needs no network access
        monkeypatch.setattr(data, "REMOTE_URL", "/does/not/exist")

    data.SpecimenCollection(checkout, fetch=True)

    assert specimen.read_text() == "original"


def test_specimen_collection_fetch_damaged_repository(
    tmp_path, remote, monkeypatch
) -> None:
    """A checkout whose repository is damaged is cloned again."""
    checkout = tmp_path / "test-data"
    data.SpecimenCollection(checkout, fetch=True)
    data._rmtree(checkout / ".git" / "objects")

    data.SpecimenCollection(checkout, fetch=True)

    assert (checkout / "specimen.xml").read_text() == "original"


def test_specimen_collection_fetch_refresh(tmp_path, remote) -> None:
    """Only `refresh` retrieves new commits."""
    checkout = tmp_path / "test-data"
    data.SpecimenCollection(checkout, fetch=True)

    with git.Repo.clone_from(remote, tmp_path / "other") as other:
        (tmp_path / "other" / "specimen.xml").write_text("updated")
        other.index.add(["specimen.xml"])
        other.index.commit("Update")
        other.remotes["origin"].push("main")

    data.SpecimenCollection(checkout, fetch=True)
    assert (checkout / "specimen.xml").read_text() == "original"

    data.SpecimenCollection(checkout, fetch=True, refresh=True)
    assert (checkout / "specimen.xml").read_text() == "updated"


def test_specimen_collection_fetch_refresh_keeps_checkout_on_error(
    tmp_path, remote, monkeypatch
) -> None:
    """A failed refresh does not remove a good checkout."""
    checkout = tmp_path / "test-data"
    data.SpecimenCollection(checkout, fetch=True)
    remote.rename(tmp_path / "moved.git")

    with pytest.raises(git.GitCommandError):
        data.SpecimenCollection(checkout, fetch=True, refresh=True)

    assert (checkout / "specimen.xml").read_text() == "original"


def test_specimen_collection_fetch_foreign_directory(tmp_path, remote) -> None:
    """A non-empty directory that is not a repository is not overwritten."""
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "mine.txt").write_text("keep")

    with pytest.raises(FileExistsError):
        data.SpecimenCollection(tmp_path / "files", fetch=True)

    assert (tmp_path / "files" / "mine.txt").read_text() == "keep"
