# sdmx: Statistical data and metadata exchange

[![Documentation](https://img.shields.io/badge/docs-github--pages-blue)](https://minh-dng.github.io/sdmx/)
[![Supported service status](https://img.shields.io/badge/services-status-informational)](https://minh-dng.github.io/sdmx/)
[![CI status](https://github.com/minh-dng/sdmx/actions/workflows/pytest.yaml/badge.svg)](https://github.com/minh-dng/sdmx/actions)
[![Code coverage](https://codecov.io/gh/minh-dng/sdmx/branch/main/graph/badge.svg)](https://codecov.io/gh/minh-dng/sdmx)

[Source code on GitHub](https://github.com/minh-dng/sdmx) ·
[Authors](https://github.com/minh-dng/sdmx/graphs/contributors)

`sdmx` (distributed as `sdmx1`) is a Python implementation of the
[SDMX](http://www.sdmx.org) 2.1 ([ISO 17369:2013](https://www.iso.org/standard/52500.html))
and 3.0 standards for **statistical data and metadata exchange**. The SDMX
standards are developed and used by national statistical agencies, central banks,
and international organisations.

`sdmx` can:

- Explore and retrieve data from [SDMX-REST web services](https://sdmx1.rtfd.io/en/latest/sources.html),
  including the World Bank, International Monetary Fund, Eurostat, OECD, and
  United Nations.
- Read and write SDMX-ML (XML), SDMX-JSON, and SDMX-CSV.
- Convert data and metadata into [pandas](https://pandas.pydata.org/) objects.
- Apply the [SDMX information model](https://sdmx1.rtfd.io/en/latest/implementation.html#im)
  to structure and publish data.

## Install

Install the current `main` branch directly from this repository:

```shell
python -m pip install "sdmx1 @ git+https://github.com/minh-dng/sdmx.git"
```

Each `project.version` is released as a GitHub Release after its pull request is
merged. Install a specific release wheel:

```shell
python -m pip install "https://github.com/minh-dng/sdmx/releases/download/vX.Y.Z/sdmx1-X.Y.Z-py3-none-any.whl"
```

Replace `X.Y.Z` with the release version.

## Documentation

See [the documentation](https://minh-dng.github.io/sdmx/) for `main`, built
automatically by the docs workflow on every push.

## History

`sdmx` is a fork of [pandaSDMX](https://github.com/dr-leo/pandaSDMX), in turn a
fork of [pysdmx](https://github.com/widukind/pysdmx).
