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


@pytest.fixture
def checkout(tmp_path, monkeypatch) -> Path:
    """The location of the checkout that sdmx manages."""
    path = tmp_path / "test-data"
    monkeypatch.setattr(data, "DEFAULT_DIR", path)
    return path


@pytest.mark.parametrize(
    "state", ["missing", "clean", "deleted", "modified", "no-index"]
)
def test_specimen_collection_fetch(
    tmp_path, monkeypatch, remote, checkout, state
) -> None:
    """:meth:`.SpecimenCollection.fetch` leaves a usable checkout in every state."""
    specimen = checkout / "specimen.xml"
    if state != "missing":
        data.SpecimenCollection(checkout, fetch=True)
        if state == "deleted":
            specimen.unlink()
        elif state == "no-index":
            (checkout / ".git" / "index").unlink()
            specimen.unlink()
        elif state == "modified":
            specimen.write_text("changed")

        # Repair needs no network access
        monkeypatch.setattr(data, "REMOTE_URL", "/does/not/exist")

    data.SpecimenCollection(checkout, fetch=True)

    assert specimen.read_text() == "original"


def test_specimen_collection_fetch_damaged_repository(remote, checkout) -> None:
    """A managed checkout whose repository is damaged is cloned again."""
    data.SpecimenCollection(checkout, fetch=True)
    data._rmtree(checkout / ".git" / "objects")

    data.SpecimenCollection(checkout, fetch=True)

    assert (checkout / "specimen.xml").read_text() == "original"


def test_specimen_collection_fetch_refresh(tmp_path, remote, checkout) -> None:
    """Only `refresh` retrieves new commits."""
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
    tmp_path, remote, checkout
) -> None:
    """A failed refresh does not remove a good checkout."""
    data.SpecimenCollection(checkout, fetch=True)
    remote.rename(tmp_path / "moved.git")

    with pytest.raises(git.GitCommandError):
        data.SpecimenCollection(checkout, fetch=True, refresh=True)

    assert (checkout / "specimen.xml").read_text() == "original"


def test_specimen_collection_fetch_failed_clone_keeps_directory(
    tmp_path, checkout, monkeypatch
) -> None:
    """A managed directory is removed only after the new clone succeeds."""
    checkout.mkdir()
    (checkout / "mine.txt").write_text("keep")
    monkeypatch.setattr(data, "REMOTE_URL", str(tmp_path / "missing.git"))

    with pytest.raises(git.GitCommandError):
        data.SpecimenCollection(checkout, fetch=True)

    assert (checkout / "mine.txt").read_text() == "keep"
    assert len(list(tmp_path.glob("test-data.*"))) == 0


def test_specimen_collection_fetch_failed_publication_keeps_directory(
    tmp_path, remote, checkout, monkeypatch
) -> None:
    """A failed rename restores the directory that was there before the clone."""
    checkout.mkdir()
    (checkout / "mine.txt").write_text("keep")
    rename = Path.rename

    def fail_publication(self, target):
        if target == checkout and not (self / "mine.txt").exists():
            raise PermissionError("Cannot publish clone")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_publication)
    with pytest.raises(PermissionError, match="Cannot publish clone"):
        data.SpecimenCollection(checkout, fetch=True)

    assert (checkout / "mine.txt").read_text() == "keep"
    assert list(tmp_path.glob("test-data.*")) == []


@pytest.mark.parametrize("broken_git", [False, True])
def test_specimen_collection_fetch_custom_directory(
    tmp_path, remote, broken_git
) -> None:
    """A directory that sdmx does not manage and cannot use is not changed."""
    custom = tmp_path / "files"
    custom.mkdir()
    (custom / "mine.txt").write_text("keep")
    if broken_git:
        (custom / ".git").mkdir()
        (custom / ".git" / "HEAD").write_text("garbage")

    with pytest.raises(FileExistsError, match="does not change"):
        data.SpecimenCollection(custom, fetch=True)

    assert (custom / "mine.txt").read_text() == "keep"
    assert (custom / ".git").exists() == broken_git


@pytest.mark.parametrize("refresh", [False, True])
def test_specimen_collection_fetch_custom_repository(tmp_path, remote, refresh) -> None:
    """A repository that sdmx does not manage is never reset."""
    custom = tmp_path / "custom"
    data.SpecimenCollection(custom, fetch=True)  # Empty directory: cloned
    (custom / "specimen.xml").write_text("my edit")

    with pytest.raises(RuntimeError, match="has local changes"):
        data.SpecimenCollection(custom, fetch=True, refresh=refresh)

    assert (custom / "specimen.xml").read_text() == "my edit"


def test_specimen_collection_fetch_custom_repository_refresh(tmp_path, remote) -> None:
    """A clean repository that sdmx does not manage cannot be refreshed."""
    custom = tmp_path / "custom"
    data.SpecimenCollection(custom, fetch=True)

    with pytest.raises(RuntimeError, match="cannot be refreshed"):
        data.SpecimenCollection(custom, fetch=True, refresh=True)

    # A clean repository is otherwise used as is
    data.SpecimenCollection(custom, fetch=True)
