"""Local cache of the official SDMX-ML schemas (XSD documents) and sample messages."""

import importlib.resources
import io
import logging
import time
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from shutil import copyfile, rmtree
from typing import TYPE_CHECKING

from sdmx.format import Version

if TYPE_CHECKING:
    import requests

log = logging.getLogger(__name__)

#: Retrieve the bytes of the zip archive of the sdmx-twg/sdmx-ml repository at a Git
#: tag, for instance "v2.1". The GitHub adapter is :func:`github_zipball`; tests
#: substitute a function that returns an in-memory archive.
Fetch = Callable[[str], bytes]

#: HTTP status codes indicating a transient failure, and so worth retrying: request
#: timeout, rate limited, and server errors.
_TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

#: XSD documents bundled with the package (see the LICENSE alongside them) and added to
#: the "schemas" directory of each cache entry. The SDMX-ML schemas reference XHTML
#: structured content, so SDMXMessage.xsd must be accompanied by a copy of
#: :file:`xhtml1-strict.xsd`, which itself imports :file:`xml.xsd`. Both files
#: (copyright 1998-2002 W3C, redistributed under the W3C Software and Document License)
#: are bundled, so no request to w3.org is needed.
_BUNDLED_SCHEMAS = ("xml.xsd", "xhtml1-strict.xsd")

_GIT_TAG = {Version["2.1"]: "v2.1", Version["3.0.0"]: "v3.0.0"}


def _fetch_with_retries(
    url: str, headers: "dict[str, str] | None" = None
) -> "requests.Response":
    """Retrieve `url` with :func:`requests.get`, retrying transient failures.

    Used for downloading schema files, where CI runners occasionally see refused
    connections, truncated responses, rate limiting, or server errors. Transport
    exceptions and transient HTTP status codes (see :data:`_TRANSIENT_STATUSES`) are
    retried. The response content is fully read within the retry block, so a truncated
    transfer is retried instead of producing a corrupt file. Each request has an
    explicit timeout, so a stalled server cannot block indefinitely.

    Raises
    ------
    requests.HTTPError
        if the final response has an error (non-2xx) status code.
    """
    import requests

    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(url=url, headers=headers, timeout=30.0)
            resp.content  # Force read; e.g. truncation raises here
        except requests.exceptions.RequestException as e:
            if attempt == attempts:
                raise
            log.info(f"{e!r} fetching {url} (attempt {attempt} of {attempts})")
            time.sleep(attempt)
            continue

        if resp.status_code in _TRANSIENT_STATUSES and attempt < attempts:
            log.info(
                f"HTTP {resp.status_code} fetching {url} "
                f"(attempt {attempt} of {attempts})"
            )
            time.sleep(attempt)
            continue

        resp.raise_for_status()
        return resp
    raise AssertionError  # pragma: no cover


def github_zipball(tag: str) -> bytes:
    """:data:`Fetch` adapter for GitHub.

    codeload.github.com serves the archive directly, without the rate limit of
    api.github.com.
    """
    url = f"https://codeload.github.com/sdmx-twg/sdmx-ml/zip/refs/tags/{tag}"
    return _fetch_with_retries(url).content


def _is_complete(entry: Path) -> bool:
    """:any:`True` if `entry` holds the official schemas and the bundled XSDs."""
    schemas = entry.joinpath("schemas")
    return all(
        schemas.joinpath(name).is_file()
        for name in ("SDMXMessage.xsd", *_BUNDLED_SCHEMAS)
    )


def _build(staging: Path, zipball: bytes) -> Path:
    """Unpack `zipball` and add the bundled XSDs, all within `staging`.

    Returns the path of the unpacked repository root.
    """
    with zipfile.ZipFile(io.BytesIO(zipball)) as zf:
        if bad_member := zf.testzip():
            raise zipfile.BadZipFile(f"Bad CRC for {bad_member!r}")
        # The top-level directory within the archive; the first name list entry is
        # either that directory or a file within it
        top = zf.namelist()[0].split("/")[0]
        zf.extractall(staging)

    root = staging.joinpath(top)
    for name in _BUNDLED_SCHEMAS:
        source = importlib.resources.files("sdmx.format.xml").joinpath(
            f"schemas/{name}"
        )
        with importlib.resources.as_file(source) as path:
            copyfile(path, root.joinpath("schemas", name))
    return root


def ensure_schemas(
    version: Version,
    cache_root: Path | None = None,
    fetch: Fetch = github_zipball,
) -> Path:
    """Return the directory of a complete schema set for `version`.

    The directory is :file:`{cache_root}/sdmx-ml-{tag}`. It contains the unpacked
    sdmx-twg/sdmx-ml repository for the Git tag, and in its :file:`schemas`
    subdirectory, the bundled XSDs. No network access occurs if the directory is
    already complete; one that lacks a file, for instance from an earlier version of
    :mod:`sdmx`, is replaced.

    The directory is built in a private staging directory next to it and published
    with a single :meth:`~pathlib.Path.rename`. Concurrent callers (for instance,
    pytest-xdist workers) therefore never see or modify a partial directory; if two
    callers publish at once, one of the equivalent results is kept.

    Parameters
    ----------
    version
        :py:`Version["2.1"]` or :py:`Version["3.0.0"]`.
    cache_root
        Default: :func:`platformdirs.user_cache_path` for "sdmx".
    fetch
        Function to retrieve the archive. Default: :func:`github_zipball`.

    Raises
    ------
    zipfile.BadZipFile
        if the archive from `fetch` is corrupt. Nothing is published.
    """
    if cache_root is None:
        import platformdirs

        cache_root = platformdirs.user_cache_path("sdmx")

    tag = _GIT_TAG[version]
    entry = cache_root.joinpath(f"sdmx-ml-{tag}")
    if _is_complete(entry):
        return entry

    staging = cache_root.joinpath(f"{entry.name}.tmp{uuid.uuid4().hex}")
    staging.mkdir(parents=True)
    try:
        root = _build(staging, fetch(tag))
        if entry.exists() and not _is_complete(entry):
            log.info(f"Replace incomplete {entry}")
            try:
                entry.rename(staging.joinpath("incomplete"))
            except FileNotFoundError:
                pass  # Another caller moved it first
        try:
            root.rename(entry)
        except OSError:
            # Another caller published first; its result is equivalent
            if not _is_complete(entry):
                raise
    finally:
        rmtree(staging, ignore_errors=True)
    return entry
