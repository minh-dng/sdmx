import pytest

from sdmx import Client, Resource
from sdmx.model import v21 as model
from sdmx.source import (
    DataContentType,
    add_source,
    get_source,
    list_sources,
    sources,
)


def test_get_source(caplog):
    s1 = get_source("WB")
    assert 0 == len(caplog.messages)

    s2 = get_source("wb")
    assert "'WB' as a case-insensitive match for id 'wb'" in caplog.messages[-1]

    assert s1 == s2


def test_list_sources():
    source_ids = list_sources()
    # Correct number of sources, excluding those created for testing
    assert 36 == len(set(source_ids) - {"MOCK", "TEST"})

    # Listed alphabetically
    assert "ABS" == source_ids[0]
    assert "WB_WDI" == source_ids[-1]


def test_source_support():
    # Implicitly supported endpoint
    assert sources["ILO"].supports["categoryscheme"] is True

    # Specifically unsupported endpoint
    assert sources["ESTAT"].supports["contentconstraint"] is False

    # Explicitly supported structure-specific data
    assert sources["INEGI"].supports["structure-specific data"] is True


@pytest.mark.parametrize("source_id", ["ABS", "ABS_JSON"])
def test_abs_url(source_id):
    assert sources[source_id].url == "https://data.api.abs.gov.au/rest"


def test_abs_support():
    source = sources["ABS"]

    assert all(
        source.supports[resource]
        for resource in (
            Resource.actualconstraint,
            Resource.categoryscheme,
            Resource.contentconstraint,
        )
    )


@pytest.mark.parametrize(
    "data_format, accept",
    [
        (DataContentType.JSON, "application/json"),
        (DataContentType.XML, "application/xml"),
    ],
)
def test_abs_data_format(data_format, accept):
    request = Client("ABS").data(
        "ABS,ANA_AGG,1.0.0",
        key="....Q",
        format=data_format,
        dry_run=True,
    )

    assert request.headers["Accept"] == accept


def test_abs_data_format_with_dsd():
    """A supplied DSD does not override a JSON data format with the XML default."""
    dsd = model.DataStructureDefinition(
        id="DSD_TEST", version="1.0", maintainer=model.Agency(id="TEST")
    )
    request = Client("ABS").data(
        "ABS,ANA_AGG,1.0.0",
        dsd=dsd,
        format=DataContentType.JSON,
        dry_run=True,
    )

    assert request.headers["Accept"] == "application/json"


@pytest.mark.parametrize(
    "resource_type, resource_id",
    [
        ("dataflow", "LABOUR_ACCT_Q"),
        ("datastructure", "DS_LABOUR_ACCT_Q"),
    ],
)
def test_abs_metadata_accept_header(resource_type, resource_id):
    client = Client("ABS")

    request = client.get(
        resource_type,
        resource_id,
        params={"references": "none"},
        dry_run=True,
    )

    assert request.url == (
        f"https://data.api.abs.gov.au/rest/{resource_type}/ABS/"
        f"{resource_id}/latest?references=none"
    )
    assert request.headers["Accept"] == "application/xml"


def test_abs_metadata_accept_header_precedence():
    client = Client("ABS")

    request = client.datastructure(
        "DS_LABOUR_ACCT_Q",
        params={"references": "none"},
        headers={"X-Test": "value"},
        dry_run=True,
    )

    assert request.headers["Accept"] == "application/xml"
    assert request.headers["X-Test"] == "value"

    # An explicit content type supplied by the caller takes precedence.
    request = client.dataflow(
        "LABOUR_ACCT_Q",
        params={"references": "none"},
        headers={"accept": "application/vnd.sdmx.structure+json"},
        dry_run=True,
    )

    assert request.headers["Accept"] == "application/vnd.sdmx.structure+json"

    request = client.data(
        "LABOUR_ACCT_Q",
        key="M28...10.Q",
        dsd=model.DataStructureDefinition(),
        dry_run=True,
    )

    assert (
        request.headers["Accept"]
        == "application/vnd.sdmx.structurespecificdata+xml;version=2.1"
    )


def test_abs_url_does_not_set_metadata_accept_header():
    request = Client("ABS").get(
        url="https://data.api.abs.gov.au/rest/data/ABS,CPI,2.0.0/1.10001.10.50.M",
        dry_run=True,
    )

    assert request.headers["Accept"] == "*/*"


def test_add_source():
    profile = """{
        "id": "FOO",
        "name": "Demo source",
        "url": "https://example.org/sdmx"
        }"""
    add_source(profile)

    # JSON sources do not support metadata endpoints, by default
    profile2 = """{
        "id": "BAR",
        "data_content_type": "JSON",
        "name": "Demo source",
        "url": "https://example.org/sdmx"
        }"""
    add_source(profile2)
    assert not sources["BAR"].supports["datastructure"]
    assert not sources["BAR"].supports["metadata"]
    assert sources["BAR"].supports["data"]

    with pytest.raises(
        ValueError, match="Data source 'ECB' already defined; use override=True"
    ):
        add_source(dict(id="ECB", name="Demo source", url="https://example.com/sdmx"))


GENERIC = "application/vnd.sdmx.genericdata+xml;version=2.1"
SS = "application/vnd.sdmx.structurespecificdata+xml;version=2.1"
IMF3 = ", ".join(
    f"application/vnd.sdmx.{x}+xml; version=3.0.0"
    for x in ("data", "metadata", "structure")
)
DSD = model.DataStructureDefinition()

#: (source ID, args to Client.get, kwargs to Client.get, URL after Source.url, Accept)
REQUESTS = [
    # Default agency for structure queries, from Source.structure_agency
    ("INSEE", ("codelist", "CL"), {}, "/codelist/ALL/CL/latest", "*/*"),
    ("ISTAT", ("dataflow",), {}, "/dataflow/all/all/latest", "*/*"),
    ("OECD", ("dataflow",), {}, "/dataflow/ALL/all/latest", "*/*"),
    ("OECD3", ("dataflow",), {}, "/structure/dataflow/*/*/+", "*/*"),
    ("SGR", ("dataflow",), {}, "/dataflow/all/all/latest", "*/*"),
    ("WB", ("dataflow",), {}, "/dataflow/WBG_WITS/all/latest", "*/*"),
    ("ESTAT_COMEXT", ("codelist", "CL"), {}, "/codelist/ESTAT/CL/latest", "*/*"),
    ("ESTAT3", ("codelist", "CL"), {}, "/structure/codelist/ESTAT/CL/+", "*/*"),
    # …but not for data queries, with or without a key
    ("OECD3", ("data", "DF"), {}, "/data/*/OECD3/DF/+", "*/*"),
    ("ESTAT3", ("data", "DF"), dict(key="A.B"), "/data/*/ESTAT3/DF/+/A.B", "*/*"),
    # …and the caller's agency_id is kept
    ("INSEE", ("codelist", "CL"), dict(agency_id="X"), "/codelist/X/CL/latest", "*/*"),
    # Default headers by resource type, from Source.headers
    ("ILO", ("data", "DF"), dict(key="A.B"), "/data/DF/A.B", SS),
    ("ILO", ("dataflow", "DF"), {}, "/dataflow/ILO/DF/latest?references=all", "*/*"),
    ("IMF_DATA3", ("dataflow", "DF"), {}, "/structure/dataflow/all/DF/+", IMF3),
    # The caller's headers replace one default each
    ("ILO", ("data", "DF"), dict(key="A.B", headers={"accept": "text/plain"}), "/data/DF/A.B", "text/plain"),
    ("INSEE", ("data", "DF"), dict(key="A.B", headers={"X-Foo": "1"}), "/data/DF/A.B", GENERIC),
    # A DSD asks for structure-specific data
    ("INSEE", ("data", "DF"), dict(key="A.B", dsd=DSD), "/data/DF/A.B", SS),
    # ABS: format is for the hook; metadata and XML data are requested as XML
    ("ABS", ("dataflow", "DF"), {}, "/dataflow/ABS/DF/latest?references=all", "application/xml"),
    ("ABS", ("data", "DF"), dict(key="A.B", format=DataContentType.JSON), "/data/DF/A.B", "application/json"),
    ("ABS", ("data", "DF"), dict(key="A.B", format=DataContentType.JSON, dsd=DSD), "/data/DF/A.B", "application/json"),
    ("ABS", ("data", "DF"), dict(key="A.B", dsd=DSD), "/data/DF/A.B", SS),
    ("ABS_JSON", ("data", "DF"), dict(key="A.B", format=DataContentType.XML), "/data/DF/A.B", "application/xml"),
    # BBK: structure queries use another base URL, and have no version
    ("BBK", ("codelist", "CL"), {}, "/metadata/codelist/BBK/CL/latest", "*/*"),
    ("BBK", ("dataflow", "DF"), dict(version="1.0"), "/metadata/dataflow/BBK/DF/latest?references=all", "*/*"),
    ("BBK", ("data", "DF"), dict(key="A.B"), "/data/DF/A.B", "*/*"),
    # ESTAT: get_footer_url is for the hook; references are limited to what is supported
    ("ESTAT", ("data", "DF"), dict(key="A.B", get_footer_url=(1, 1)), "/data/DF/A.B", "*/*"),
    ("ESTAT", ("dataflow", "DF"), {}, "/dataflow/ESTAT/DF/latest?references=descendants", "*/*"),
    ("ESTAT", ("datastructure", "DS"), dict(references="parents"), "/datastructure/ESTAT/DS/latest?references=none", "*/*"),
    ("COMP", ("dataflow", "DF"), dict(params=dict(references="all")), "/dataflow/COMP/DF/latest?references=none", "*/*"),
    ("EMPL", ("categoryscheme",), {}, "/categoryscheme/EMPL/all/latest?references=descendants", "*/*"),
    ("GROW", ("datastructure", "DS"), {}, "/datastructure/GROW/DS/latest?references=descendants", "*/*"),
    # WB_WDI: trailing slash for data; its own agency for category schemes
    ("WB_WDI", ("data", "DF"), dict(key="A.B"), "/data/DF/A.B/", "*/*"),
    ("WB_WDI", ("categoryscheme",), dict(force=True), "/categoryscheme/all/all/latest?references=parentsandsiblings", "*/*"),
    ("WB_WDI", ("datastructure", "DS"), dict(force=True), "/datastructure/WB/DS/latest?references=all", "*/*"),
]  # fmt: skip


@pytest.mark.parametrize(
    "source_id, args, kwargs, path, accept",
    [pytest.param(*r, id="-".join([r[0], *r[1]])) for r in REQUESTS],
)
def test_request(recwarn, source_id, args, kwargs, path, accept) -> None:
    """The Source and Client together give the expected URL and headers."""
    request = Client(source_id).get(*args, dry_run=True, **kwargs)  # type: ignore [call-overload]

    assert sources[source_id].url + path == request.url
    assert accept == request.headers["Accept"]
    for name, value in kwargs.get("headers", {}).items():
        assert value == request.headers[name]

    # No source uses a deprecated feature of Client.get()
    assert [] == [w for w in recwarn if w.category is DeprecationWarning]


def test_hook_options_not_url_parameters() -> None:
    """Arguments for a hook are not accepted by sources that do not have the hook."""
    with pytest.raises(ValueError, match="Unexpected/unhandled parameters"):
        Client("ECB").data("DF", key="A.B", get_footer_url=(1, 1), dry_run=True)


def test_shared_source_not_modified() -> None:
    """Queries do not change the Source, which is shared by all its Clients."""
    source = sources["BBK"]
    before = source.url

    Client("BBK").codelist("CL", dry_run=True)

    assert before == source.url == Client("BBK").source.url


class TestSource:
    def test_get_url_class(self):
        """get_url_class() returns :class:`.v30.URL` as appropriate."""
        from sdmx.rest import v30

        assert issubclass(sources["ESTAT3"].get_url_class(), v30.URL)
