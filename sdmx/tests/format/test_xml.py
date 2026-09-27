import io
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest
import requests
import responses

import sdmx
from sdmx.format import Version, xml
from sdmx.format.xml.common import (
    _download_zipball,
    _extract_zipball,
    _extracted_zipball,
    _fetch_with_retries,
)
from sdmx.message import StructureMessage
from sdmx.model import v21


def test_ns_prefix():
    with pytest.raises(ValueError):
        xml.v21.ns_prefix("https://example.com")


def test_qname():
    assert f"{{{xml.v21.base_ns}/structure}}Code" == str(xml.v21.qname("str", "Code"))
    assert f"{{{xml.v30.base_ns}/structure}}Code" == str(xml.v30.qname("str", "Code"))


def test_tag_for_class():
    # ItemScheme is never written to XML; no corresponding tag name
    assert xml.v21.tag_for_class(v21.ItemScheme) is None


def test_class_for_tag():
    assert xml.v30.class_for_tag("str:DataStructure") is not None


@pytest.mark.network
@pytest.mark.parametrize("version", ["2.1", "3.0.0"])
def test_install_schemas(installed_schemas, version):
    """Test that XSD files are downloaded and ready for use in validation."""
    # Look for a couple of the expected files
    for schema_doc in ("SDMXCommon.xsd", "SDMXMessage.xsd"):
        assert installed_schemas.joinpath(version, schema_doc).exists()


@pytest.mark.network
def test_install_schemas_in_user_cache():
    """Test that XSD files are downloaded and ready for use in validation."""
    import platformdirs

    cache_dir = platformdirs.user_cache_path("sdmx") / "2.1"
    sdmx.install_schemas()

    # Look for a couple of the expected files
    files = ["SDMXCommon.xsd", "SDMXMessage.xsd"]
    for schema_doc in files:
        doc = cache_dir.joinpath(schema_doc)
        assert doc.exists(), (cache_dir, sorted(cache_dir.glob("*")))


@pytest.mark.parametrize("version", ["1", 1, None])
def test_install_schemas_invalid_version(version):
    """Ensure invalid versions throw ``NotImplementedError``."""
    with pytest.raises(NotImplementedError):
        sdmx.install_schemas(version=version)


GH_API = "https://api.github.com/repos/sdmx-twg/sdmx-ml"


@pytest.fixture
def cache_dir(monkeypatch, tmp_path):
    """Redirect the sdmx user cache to a temporary directory."""
    monkeypatch.setattr("platformdirs.user_cache_path", lambda app: tmp_path)
    return tmp_path


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip the backoff delays in :func:`._fetch_with_retries`."""
    monkeypatch.setattr(time, "sleep", lambda _: None)


def _zipball_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("sdmx-ml-v2.1/schemas/SDMXMessage.xsd", "<xs:schema/>")
    return buf.getvalue()


def _mock_gh_api(mock: responses.RequestsMock) -> None:
    """Mock the GitHub API responses for downloading the 2.1 schema zipball."""
    mock.get(
        f"{GH_API}/releases/tags/v2.1",
        json={"zipball_url": f"{GH_API}/zipball/v2.1"},
    )
    mock.get(f"{GH_API}/zipball/v2.1", body=_zipball_bytes())


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
        mock.get(f"{GH_API}/foo", **response_kwargs)
        mock.get(f"{GH_API}/foo", json={"ok": True})
        result = _fetch_with_retries(f"{GH_API}/foo")

        assert result.json() == {"ok": True}
        assert len(mock.calls) == 2


def test_fetch_with_retries_exhausted(no_sleep):
    """After the final attempt, a transient HTTP status raises HTTPError."""
    with responses.RequestsMock() as mock:
        for _ in range(3):
            mock.get(f"{GH_API}/foo", status=500)
        with pytest.raises(requests.HTTPError):
            _fetch_with_retries(f"{GH_API}/foo")

        assert len(mock.calls) == 3


def test_fetch_with_retries_non_transient():
    """A non-transient HTTP status is not retried."""
    with responses.RequestsMock() as mock:
        mock.get(f"{GH_API}/foo", status=404)
        with pytest.raises(requests.HTTPError):
            _fetch_with_retries(f"{GH_API}/foo")

        assert len(mock.calls) == 1


def test_copy_bundled_schemas_fresh_interpreter(tmp_path):
    """The bundled schemas can be copied in a process where importlib.resources is not
    pre-imported by something else, e.g. pytest."""
    code = """\
import sys
from pathlib import Path

from sdmx.format.xml.common import _copy_bundled_schemas

_copy_bundled_schemas(Path(sys.argv[1]))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "xhtml1-strict.xsd").exists()
    assert (tmp_path / "xml.xsd").exists()


def test_extracted_zipball_caches(no_sleep, cache_dir):
    """The zipball is downloaded once, then reused without any network access."""
    with responses.RequestsMock() as mock:
        _mock_gh_api(mock)
        result = _extracted_zipball(Version["2.1"])

    # Contents are extracted and the bundled XSDs copied alongside
    assert result.joinpath("schemas", "SDMXMessage.xsd").exists()
    assert result.joinpath("schemas", "xhtml1-strict.xsd").exists()
    assert result.joinpath("schemas", "xml.xsd").exists()

    # A second call performs no HTTP requests at all
    with responses.RequestsMock() as mock:
        assert _extracted_zipball(Version["2.1"]) == result
        assert len(mock.calls) == 0


def test_extracted_zipball_repairs_corrupt_cache(no_sleep, cache_dir):
    """A corrupt cached zipball is discarded and replaced by a fresh download."""
    with responses.RequestsMock() as mock:
        _mock_gh_api(mock)
        first = _extracted_zipball(Version["2.1"])
        assert len(mock.calls) == 2

        # Corrupt the cached zipball
        cache_dir.joinpath("sdmx-ml-v2.1.zip").write_bytes(b"not a zip file")

        second = _extracted_zipball(Version["2.1"])
        assert len(mock.calls) == 4  # Release lookup and zipball, twice

    assert first == second
    assert first.joinpath("schemas", "SDMXMessage.xsd").exists()


def test_extracted_zipball_force(no_sleep, cache_dir):
    """force=True re-downloads and replaces the extracted directory in place."""
    with responses.RequestsMock() as mock:
        _mock_gh_api(mock)
        first = _extracted_zipball(Version["2.1"])
        # Modify the extracted content, to detect whether it is replaced
        first.joinpath("schemas", "SDMXMessage.xsd").write_text("corrupted")

        second = _extracted_zipball(Version["2.1"], force=True)
        assert len(mock.calls) == 4  # Release lookup and zipball, twice

    assert first == second
    assert second.joinpath("schemas", "SDMXMessage.xsd").read_text() == "<xs:schema/>"

    # No temporary files or directories are left behind
    assert sorted(p.name for p in cache_dir.iterdir()) == [
        "sdmx-ml-v2.1",
        "sdmx-ml-v2.1.zip",
    ]


def test_extract_zipball_concurrent(no_sleep, cache_dir, monkeypatch):
    """If another caller publishes the extraction while this one works, keep theirs."""
    with responses.RequestsMock() as mock:
        _mock_gh_api(mock)
        zipball = cache_dir.joinpath("sdmx-ml-v2.1.zip")
        _download_zipball(zipball, "v2.1")

    original_extractall = zipfile.ZipFile.extractall

    def extractall_and_publish(self, *args, **kwargs):
        original_extractall(self, *args, **kwargs)
        # Simulate a concurrent caller publishing its extraction while this one extracts
        top = self.namelist()[0].split("/")[0]
        Path(*args).joinpath(top).rename(zipball.parent.joinpath(top))

    monkeypatch.setattr(zipfile.ZipFile, "extractall", extractall_and_publish)

    result = _extract_zipball(zipball)

    assert result.joinpath("schemas", "SDMXMessage.xsd").exists()

    # No temporary files or directories are left behind
    assert sorted(p.name for p in cache_dir.iterdir()) == [
        "sdmx-ml-v2.1",
        "sdmx-ml-v2.1.zip",
    ]


def test_extract_zipball_rename_race(no_sleep, cache_dir, monkeypatch):
    """A caller that loses the race to publish keeps the winner's extraction."""
    with responses.RequestsMock() as mock:
        _mock_gh_api(mock)
        zipball = cache_dir.joinpath("sdmx-ml-v2.1.zip")
        _download_zipball(zipball, "v2.1")

    result = zipball.parent.joinpath("sdmx-ml-v2.1")
    state = {"published": False}
    original_rename = Path.rename

    def rename(self, target):
        if target == result and not state["published"]:
            state["published"] = True
            # Simulate a concurrent caller publishing its extraction first…
            with zipfile.ZipFile(zipball) as zf:
                top = zf.namelist()[0].split("/")[0]
                other = zipball.parent.joinpath("other.tmp")
                zf.extractall(other)
                original_rename(other.joinpath(top), result)
            shutil.rmtree(other, ignore_errors=True)
            # …then this caller loses the race to move its own copy into place
            raise OSError(17, "File exists")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)

    assert _extract_zipball(zipball) == result
    assert result.joinpath("schemas", "SDMXMessage.xsd").exists()

    # No temporary files or directories are left behind
    assert sorted(p.name for p in cache_dir.iterdir()) == [
        "sdmx-ml-v2.1",
        "sdmx-ml-v2.1.zip",
    ]


def test_extract_zipball_force_race(no_sleep, cache_dir, monkeypatch):
    """A forced replacement racing with another publisher keeps the other's result."""
    with responses.RequestsMock() as mock:
        _mock_gh_api(mock)
        zipball = cache_dir.joinpath("sdmx-ml-v2.1.zip")
        _download_zipball(zipball, "v2.1")

    # Publish an initial extraction, then corrupt its content
    first = _extract_zipball(zipball)
    first.joinpath("schemas", "SDMXMessage.xsd").write_text("stale content")

    result = zipball.parent.joinpath("sdmx-ml-v2.1")
    state = {"simulated": False}
    original_rename = Path.rename

    def rename(self, target):
        if (
            not state["simulated"]
            and self.name.startswith(f"{zipball.name}.tmp")
            and Path(target) == result
        ):
            state["simulated"] = True
            # Simulate a concurrent caller publishing while this forced replacement
            # moves its own copy into place…
            with zipfile.ZipFile(zipball) as zf:
                top = zf.namelist()[0].split("/")[0]
                other = zipball.parent.joinpath("other.tmp")
                zf.extractall(other)
                original_rename(other.joinpath(top), result)
            shutil.rmtree(other, ignore_errors=True)
            # …so that this caller's replacement fails
            raise OSError(66, "Directory not empty")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)

    second = _extract_zipball(zipball, force=True)

    assert second == result
    # The concurrent caller's fresh content is kept
    assert second.joinpath("schemas", "SDMXMessage.xsd").read_text() == "<xs:schema/>"

    # No temporary files or directories are left behind
    assert sorted(p.name for p in cache_dir.iterdir()) == [
        "sdmx-ml-v2.1",
        "sdmx-ml-v2.1.zip",
    ]


@pytest.mark.flaky(reruns=5)
@pytest.mark.network
@pytest.mark.parametrize(
    "parts",
    [
        ("v21", "xml", "common", "common.xml"),
        ("v21", "xml", "demography", "demography.xml"),
        ("v21", "xml", "demography", "esms.xml"),
        ("ECB_EXR", "common.xml"),
        ("ECB_EXR", "ng-structure-full.xml"),
        ("ECB_EXR", "ng-structure.xml"),
        ("v21", "xml", "query", "query_cl_all.xml"),
        ("v21", "xml", "query", "response_cl_all.xml"),
        ("v21", "xml", "query", "query_esms_children.xml"),
        ("v21", "xml", "query", "response_esms_children.xml"),
    ],
)
def test_validate_xml_from_v2_1_samples(tmp_path, specimen, installed_schemas, parts):
    """Use official samples to ensure validation of v2.1 messages works correctly."""
    # Samples are somewhat spread out, and some are known broken so we pick a bunch
    with specimen(str(Path(*parts))) as sample:
        assert sdmx.validate_xml(sample, installed_schemas, version="2.1")


@pytest.fixture(scope="module")
def v30_zipball_path(installed_schemas):
    yield _extracted_zipball(Version["3.0.0"])


@pytest.mark.flaky(reruns=5)
@pytest.mark.network
@pytest.mark.parametrize(
    "parts",
    [
        # Samples are somewhat spread out, and some are known broken so we pick a bunch
        ("Codelist", "codelist.xml"),
        ("Codelist", "codelist - extended.xml"),
        ("Concept Scheme", "conceptscheme.xml"),
        ("Data Structure Definition", "ECB_EXR.xml"),
        ("Dataflow", "dataflow.xml"),
        ("Geospatial", "geospatial_geographiccodelist.xml"),
    ],
)
def test_validate_xml_from_v3_0_samples(installed_schemas, v30_zipball_path, parts):
    """Use official samples to ensure validation of v3.0 messages works correctly."""

    assert sdmx.validate_xml(
        v30_zipball_path.joinpath("samples", *parts), installed_schemas, version="3.0.0"
    )


@pytest.mark.flaky(reruns=5)
@pytest.mark.network
def test_validate_xml_invalid_doc(tmp_path, installed_schemas):
    """Ensure that an invalid document fails validation."""
    msg_path = tmp_path / "invalid.xml"

    # Generate a codelist to form a StructureMessage
    ECB = v21.Agency(id="ECB")

    cl = v21.Codelist(
        id="CL_COLLECTION",
        version="1.0",
        is_final=False,
        is_external_reference=False,
        maintainer=ECB,
        name={"en": "Collection indicator code list"},
    )

    # Add items
    CL_ITEMS = [
        dict(id="A", name={"en": "Average of observations through period"}),
        dict(id="B", name={"en": "Beginning of period"}),
        dict(id="B1", name={"en": "Child code of B"}),
    ]
    for info in CL_ITEMS:
        cl.items[info["id"]] = v21.Code(**info)

    msg = StructureMessage(codelist={cl.id: cl})

    msg_path.write_bytes(sdmx.to_xml(msg))

    assert sdmx.validate_xml(msg_path, schema_dir=installed_schemas.joinpath("2.1"))


def test_validate_xml_invalid_message_type(installed_schemas):
    """Ensure that an invalid document fails validation."""
    # Create a mangled structure message with its outmost tag changed to be invalid
    msg = StructureMessage()
    invalid_msg = io.BytesIO(
        re.sub(b"mes:Structure([ >])", rb"mes:FooBar\1", sdmx.to_xml(msg))
    )

    with pytest.raises(NotImplementedError, match="Validate non-SDMX root.*FooBar>"):
        sdmx.validate_xml(invalid_msg, installed_schemas)


@pytest.mark.parametrize("version", ["1", 1, None])
def test_validate_xml_invalid_version(version):
    """Ensure validation of invalid versions throw ``NotImplementedError``."""
    with pytest.raises(NotImplementedError):
        # This message doesn't exist, but the version should throw before it is used.
        sdmx.validate_xml("samples/common/common.xml", version=version)


def test_validate_xml_max_errors(caplog, installed_schemas):
    """Test :py:`validate_xml(..., max_errors=...)`."""
    msg = StructureMessage()
    invalid_msg = io.BytesIO(
        re.sub(b"<(mes:Structures)/>", rb"<\1><Foo/></\1><Bar/>", sdmx.to_xml(msg))
    )

    # Without max_errors, 2 messages are logged
    sdmx.validate_xml(invalid_msg, installed_schemas)
    assert 2 == len(caplog.messages)
    caplog.clear()

    # With the argument, only 1 message is logged
    sdmx.validate_xml(invalid_msg, installed_schemas, max_errors=1)
    assert 1 == len(caplog.messages)


def test_validate_xml_no_schemas(tmp_path, specimen):
    """Check that supplying an invalid schema path will raise ``ValueError``."""
    with specimen("IPI-2010-A21-structure.xml", opened=False) as msg_path:
        with pytest.raises(FileNotFoundError):
            sdmx.validate_xml(msg_path, schema_dir=tmp_path)
