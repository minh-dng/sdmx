from functools import lru_cache
from io import BytesIO, StringIO
from typing import TYPE_CHECKING

import pandas as pd
import pytest

import sdmx.message
from sdmx import to_sdmx
from sdmx.model import common
from sdmx.reader.csv import Handler, NotHandled, Reader

if TYPE_CHECKING:
    from sdmx.model import v30


class TestHandler:
    def test_abc(self):
        with pytest.raises(TypeError):
            Handler()

    def test_repr(self):
        assert "<NotHandled>" == repr(NotHandled())


class TestDataFrameConverter:
    @pytest.fixture
    def df(self) -> pd.DataFrame:
        """Equivalent to the content of v21/csv/example-01.csv"""
        buf = StringIO("""
STRUCTURE,STRUCTURE_ID,ACTION,DIM_1,DIM_2,DIM_3,OBS_VALUE,ATTR_2,ATTR_3,ATTR_1,UPDATED
dataflow,ESTAT:NA_MAIN(1.6.0),I,A,B,2014-01,12.4,Y,"Normal, special and other values",N,2021-01-22T13:15:41Z
dataflow,ESTAT:NA_MAIN(1.6.0),I,A,B,2014-02,10.8,Y,"Normal, special and other values",Y,2021-01-22T13:15:41Z
""")
        return pd.read_csv(buf)

    def test_to_sdmx(self, df) -> None:
        """:class:`.DataFrameConverter` can be used through :func:`.to_sdmx`."""
        dfd = get_dfd()

        result = to_sdmx(df, structure=dfd)  # Function runs

        assert isinstance(result, sdmx.message.DataMessage)  # Returns a data set
        assert 1 == len(result.data)  # Message has 1 data set
        ds = result.data[0]

        assert 2 == len(ds)  # Data set has 2 observations
        o0 = ds.obs[0]

        assert 12.4 == o0.value  # Observation has an expected value
        assert (  # Observation has the expected key
            dfd.structure.make_key(
                common.Key, dict(DIM_1="A", DIM_2="B", DIM_3="2014-01")
            )
            == o0.dimension
        )


class TestReader:
    @pytest.mark.parametrize("target", ["DATAFLOW", "DataProvision", "datastructure"])
    def test_case_insensitive_structure_target(self, target: str) -> None:
        content = f"""STRUCTURE,STRUCTURE_ID,ACTION,DIM_1,DIM_2,DIM_3,OBS_VALUE
{target},ESTAT:NA_MAIN(1.6.0),i,A,B,2014-01,12.4
""".encode()

        result = Reader().convert(BytesIO(content), structure=get_dfd())

        assert result.data[0].obs[0].value == "12.4"
        assert result.data[0].action is common.ActionType.information

    def test_missing_action_column_defaults_to_information(self) -> None:
        content = b"""STRUCTURE,STRUCTURE_ID,DIM_1,DIM_2,DIM_3,OBS_VALUE
dataflow,ESTAT:NA_MAIN(1.6.0),A,B,2014-01,12.4
"""

        result = Reader().convert(BytesIO(content), structure=get_dfd())

        assert result.data[0].action is common.ActionType.information

    def test_invalid_structure_target(self) -> None:
        content = b"""STRUCTURE,STRUCTURE_ID,ACTION,DIM_1,DIM_2,DIM_3,OBS_VALUE\nINVALID,ESTAT:NA_MAIN(1.6.0),I,A,B,2014-01,12.4\n"""

        with pytest.raises(ValueError, match="Invalid target value 'INVALID'"):
            Reader().convert(BytesIO(content), structure=get_dfd())

    def test_missing_action_target(self) -> None:
        content = b"""STRUCTURE,STRUCTURE_ID,ACTION,DIM_1,DIM_2,DIM_3,OBS_VALUE
dataflow,ESTAT:NA_MAIN(1.6.0),,A,B,2014-01,12.4
"""

        with pytest.raises(ValueError, match="Invalid target value ''"):
            Reader().convert(BytesIO(content), structure=get_dfd())

    def test_missing_structure_id_target(self) -> None:
        content = b"""STRUCTURE,STRUCTURE_ID,ACTION,DIM_1,DIM_2,DIM_3,OBS_VALUE
dataflow,,I,A,B,2014-01,12.4
"""

        with pytest.raises(
            ValueError, match="Invalid target value ''; expected a non-empty string"
        ):
            Reader().convert(BytesIO(content), structure=get_dfd())

    def test_short_row(self) -> None:
        content = b"""STRUCTURE,STRUCTURE_ID,ACTION,DIM_1,DIM_2,DIM_3,OBS_VALUE
dataflow,ESTAT:NA_MAIN(1.6.0),I,A,B,2014-01
"""

        with pytest.raises(ValueError, match="expected 7 fields, got 6"):
            Reader().convert(BytesIO(content), structure=get_dfd())

    def test_subdelimiter_header(self) -> None:
        reader = Reader()
        content = b"""STRUCTURE[+],STRUCTURE_ID,ACTION,DIM_1,DIM_2,DIM_3,OBS_VALUE
dataflow,ESTAT:NA_MAIN(1.6.0),I,A,B,2014-01,12.4
"""

        reader.convert(BytesIO(content), structure=get_dfd())

        assert reader.options.delimiter_sub == "+"

    @pytest.mark.parametrize(
        "mt, expected",
        [
            ("foo", False),
            ("application/vnd.sdmx.data+csv; version=1.0.0", True),
            ("application/vnd.sdmx.metadata+csv; version=2.0.0", True),
        ],
    )
    def test_handles_media_type(self, mt, expected) -> None:
        with pytest.warns(DeprecationWarning, match="use Converter.handles"):
            assert expected is Reader.handles_media_type(mt)

    @pytest.mark.parametrize(
        "content, exc_text",
        (
            (b"DATAFLOW,DIM_1,OBS_VALUE", "'DATAFLOW' in line 1, field 1"),
            (b"STRUCTURE,ACTION,DIM_1,OBS_VALUE", "'ACTION' in line 1, field 2"),
        ),
    )
    def test_inspect_header0(self, content, exc_text) -> None:
        with pytest.raises(ValueError, match=f"Invalid SDMX-CSV 2.0.0: {exc_text}"):
            Reader().convert(BytesIO(content))

    @pytest.mark.parametrize("value, expected", [(".csv", True), (".xlsx", False)])
    def test_supports_suffix(self, value, expected) -> None:
        with pytest.warns(DeprecationWarning, match="use Converter.handles"):
            assert expected is Reader.supports_suffix(value)


@lru_cache
def get_dfd(n_measure: int = 1) -> "v30.Dataflow":
    from sdmx.model import common, v30

    m = common.Agency(id="ESTAT")
    dsd = v30.DataStructureDefinition(maintainer=m)
    dsd.dimensions.append(v30.Dimension(id="DIM_1"))
    dsd.dimensions.append(v30.Dimension(id="DIM_2"))
    dsd.dimensions.append(v30.Dimension(id="DIM_3"))

    if n_measure == 1:
        dsd.measures.append(v30.Measure(id="OBS_VALUE"))
    else:
        for i in range(1, n_measure + 1):
            dsd.measures.append(v30.Measure(id=f"OBS_VALUE{i}"))

    dsd.attributes.append(common.DataAttribute(id="ATTR_2"))
    dsd.attributes.append(common.DataAttribute(id="ATTR_3"))
    dsd.attributes.append(common.DataAttribute(id="ATTR_1"))

    dfd = v30.Dataflow(id="NA_MAIN", maintainer=m, version="1.6.0", structure=dsd)

    return dfd


@pytest.mark.parametrize_specimens("path", format="csv")
def test_read_specimen(path) -> None:
    """Test that the samples from the SDMX-CSV spec can be read."""
    import sdmx

    if path.stem in ("example-02", "example-03"):
        dfd: "v30.Dataflow" = get_dfd(2)
    else:
        dfd = get_dfd()

    kwargs: dict = dict(structure=dfd)

    if path.stem == "example-04":
        kwargs.update(delimiter=";")

    result = sdmx.read_sdmx(path, **kwargs)

    # # DEBUG
    # from icecream import ic
    #
    # ic(result)
    # for i, ds in enumerate(result.data):
    #     try:
    #         ic(i, ds.action, sdmx.to_pandas(ds, attributes="dsgo"))
    #     except Exception as e:
    #         ic(e)
    #         pass

    del result
