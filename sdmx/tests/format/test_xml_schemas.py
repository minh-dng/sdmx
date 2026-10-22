import io
import time
import zipfile
from pathlib import Path

import pytest
import requests
import responses
from filelock import FileLock

from sdmx.format import Version
from sdmx.format.xml import schemas
from sdmx.format.xml.schemas import (
    _BUNDLED_SCHEMAS,
    _MANIFEST,
    _fetch_with_retries,
    ensure_schemas,
    github_zipball,
)

URL = "https://example.com/foo"


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip the backoff delays in :func:`._fetch_with_retries`."""
    monkeypatch.setattr(time, "sleep", lambda _: None)


def zipball(content: str = "<xs:schema/>") -> bytes:
    """Bytes of an archive like the one served by GitHub: one top-level directory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("sdmx-ml-2.1/schemas/SDMXMessage.xsd", content)
        zf.writestr("sdmx-ml-2.1/schemas/SDMXCommon.xsd", "<xs:schema/>")
    return buf.getvalue()


class FakeFetch:
    """In-memory :data:`.Fetch` adapter that counts its calls."""

    def __init__(self, data: bytes | None = None):
        self.data = zipball() if data is None else data
        self.tags: list[str] = []

    def __call__(self, tag: str) -> bytes:
        self.tags.append(tag)
        return self.data


def names(path: Path) -> list[str]:
    # Windows removes the lock file when releasing it; Unix leaves it in place.
    return sorted(p.name for p in path.iterdir() if p.suffix != ".lock")


def test_ensure_schemas(tmp_path):
    """The entry has the archive contents and the bundled XSDs, and nothing else."""
    fetch = FakeFetch()

    entry = ensure_schemas(Version["2.1"], tmp_path, fetch)

    assert entry == tmp_path / "sdmx-ml-v2.1"
    assert fetch.tags == ["v2.1"]
    assert entry.joinpath("schemas", "SDMXMessage.xsd").read_text() == "<xs:schema/>"
    for name in _BUNDLED_SCHEMAS:
        assert entry.joinpath("schemas", name).is_file()
    assert names(tmp_path) == ["sdmx-ml-v2.1"]  # No staging files left


def test_ensure_schemas_cached(tmp_path):
    """A complete entry is reused without calling `fetch`."""
    entry = ensure_schemas(Version["2.1"], tmp_path, FakeFetch())

    fetch = FakeFetch()
    assert ensure_schemas(Version["2.1"], tmp_path, fetch) == entry
    assert fetch.tags == []


@pytest.mark.parametrize(
    "missing", ["SDMXMessage.xsd", "SDMXCommon.xsd", *_BUNDLED_SCHEMAS]
)
def test_ensure_schemas_rebuilds_incomplete(tmp_path, missing):
    """An entry lacking any XSD document is replaced, not completed in place."""
    entry = ensure_schemas(Version["2.1"], tmp_path, FakeFetch())
    entry.joinpath("schemas", missing).unlink()
    entry.joinpath("stale.txt").write_text("from an earlier build")

    fetch = FakeFetch(zipball("new"))
    assert ensure_schemas(Version["2.1"], tmp_path, fetch) == entry

    assert fetch.tags == ["v2.1"]
    assert entry.joinpath("schemas", "SDMXMessage.xsd").read_text() == "new"
    assert entry.joinpath("schemas", missing).is_file()
    assert not entry.joinpath("stale.txt").exists()
    assert names(tmp_path) == ["sdmx-ml-v2.1"]


@pytest.mark.parametrize(
    "data",
    [
        b"not a zip file",
        # Valid ZIP metadata, but a member with a wrong CRC
        zipball().replace(b"<xs:schema/>", b"<xs:broken/>"),
    ],
    ids=["not-zip", "bad-crc"],  # Bytes contain timestamps: unstable IDs under xdist
)
def test_ensure_schemas_corrupt_download(tmp_path, data):
    """A corrupt archive publishes nothing and leaves a complete entry untouched."""
    with pytest.raises(zipfile.BadZipFile):
        ensure_schemas(Version["2.1"], tmp_path, FakeFetch(data))
    assert names(tmp_path) == []

    entry = ensure_schemas(Version["2.1"], tmp_path, FakeFetch())
    entry.joinpath("schemas", "xml.xsd").unlink()
    with pytest.raises(zipfile.BadZipFile):
        ensure_schemas(Version["2.1"], tmp_path, FakeFetch(data))
    assert names(tmp_path) == ["sdmx-ml-v2.1"]


def test_ensure_schemas_rebuilds_unlisted(tmp_path):
    """An entry without a manifest, as built by an earlier version, is replaced."""
    entry = ensure_schemas(Version["2.1"], tmp_path, FakeFetch())
    entry.joinpath(_MANIFEST).unlink()

    fetch = FakeFetch()
    assert ensure_schemas(Version["2.1"], tmp_path, fetch) == entry
    assert fetch.tags == ["v2.1"]
    assert entry.joinpath(_MANIFEST).is_file()


def test_ensure_schemas_waits_for_other_repair(tmp_path, monkeypatch):
    """A caller that finds an entry incomplete reuses another caller's repair.

    Interleaving: this caller sees the incomplete entry, then another caller repairs it
    before this one gets the lock. This caller must not replace the repaired entry.
    """
    entry = ensure_schemas(Version["2.1"], tmp_path, FakeFetch())
    entry.joinpath("schemas", "xml.xsd").unlink()
    other = FakeFetch(zipball("other"))

    class LockAfterOther(FileLock):
        def acquire(self, *args, **kwargs):
            monkeypatch.undo()  # Only once
            ensure_schemas(Version["2.1"], tmp_path, other)
            return super().acquire(*args, **kwargs)

    monkeypatch.setattr(schemas, "FileLock", LockAfterOther)
    ours = FakeFetch(zipball("ours"))

    assert ensure_schemas(Version["2.1"], tmp_path, ours) == entry

    assert ours.tags == []
    assert other.tags == ["v2.1"]
    assert entry.joinpath("schemas", "SDMXMessage.xsd").read_text() == "other"
    assert names(tmp_path) == ["sdmx-ml-v2.1"]


def test_github_zipball():
    """The adapter downloads the archive for a tag from codeload.github.com."""
    url = "https://codeload.github.com/sdmx-twg/sdmx-ml/zip/refs/tags/v2.1"
    content = zipball()
    with responses.RequestsMock() as mock:
        mock.get(url, body=content)
        assert github_zipball("v2.1") == content


@pytest.mark.parametrize(
    "response_kwargs",
    [
        dict(body=requests.ConnectionError()),  # Transport failure
        dict(status=503),  # Transient HTTP status
    ],
)
def test_fetch_with_retries(no_sleep, response_kwargs):
    """A transient failure is retried; the response of the next attempt is returned."""
    with responses.RequestsMock() as mock:
        mock.get(URL, **response_kwargs)
        mock.get(URL, json={"ok": True})
        result = _fetch_with_retries(URL)

        assert result.json() == {"ok": True}
        assert len(mock.calls) == 2


def test_fetch_with_retries_exhausted(no_sleep):
    """After the final attempt, a transient HTTP status raises HTTPError."""
    with responses.RequestsMock() as mock:
        for _ in range(3):
            mock.get(URL, status=500)
        with pytest.raises(requests.HTTPError):
            _fetch_with_retries(URL)

        assert len(mock.calls) == 3


def test_fetch_with_retries_non_transient():
    """A non-transient HTTP status is not retried."""
    with responses.RequestsMock() as mock:
        mock.get(URL, status=404)
        with pytest.raises(requests.HTTPError):
            _fetch_with_retries(URL)

        assert len(mock.calls) == 1
