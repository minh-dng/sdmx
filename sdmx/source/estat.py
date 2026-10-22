import logging
from tempfile import NamedTemporaryFile
from time import sleep
from urllib.parse import urlparse
from zipfile import ZipFile

import requests

from sdmx.rest import Resource
from sdmx.rest.common import RequestSpec
from sdmx.source import Source as BaseSource

log = logging.getLogger(__name__)


def handle_references_param(request: RequestSpec) -> None:
    """Handle the "references" query parameter for ESTAT and similar.

    For this parameter, the server software behind ESTAT's data source only supports
    (as of 2022-11-13) the values "children", "descendants", or "none". Other values—
    including the "all" or parentsandsiblings" used as defaults by :class:`.URL`—
    result in error messages.

    This handler, used via :meth:`.Source.modify_request`:

    - Replaces the defaults of "all" or "parentsandsiblings" set by :class:`.URL`
      with "descendants".
    - Replaces other, unsupported values with "none".
    """
    params = request.params

    # Preempt default values that would be set by URL
    if not params.get("references") and (
        (
            request.resource_type in (Resource.dataflow, Resource.datastructure)
            and request.resource_id
        )
        or request.resource_type == Resource.categoryscheme
    ):
        params["references"] = "descendants"

    # Replace unsupported values
    references = params.get("references")
    if references not in ("children", "descendants", "none", None):
        log.info(f"Replace unsupported references={references!r} with 'none'")
        params["references"] = "none"


class Source(BaseSource):
    """Handle Eurostat's mechanism for large datasets and other quirks.

    For some requests, ESTAT returns a DataMessage that has no content except for a
    ``<footer:Footer>`` element containing a URL where the data will be made available
    as a ZIP file.

    To configure :meth:`finish_message`, pass its `get_footer_url` argument to
    :meth:`.Client.get`.

    .. versionadded:: 0.2.1
    """

    _id = "ESTAT"

    hook_options = frozenset({"get_footer_url"})

    def modify_request(self, request: RequestSpec) -> None:
        """Handle the ``references`` query parameter.

        See also
        --------
        :pull:`107`, :pull:`108`
        """
        handle_references_param(request)

    def finish_message(self, message, client, request: RequestSpec):
        """Handle the initial response.

        This hook identifies the URL in the footer of the initial response,
        makes a second request (polling as indicated by *get_footer_url*), and
        returns a new DataMessage with the parsed content.

        Other Parameters
        ----------------
        get_footer_url : (int, int)
            Argument to :meth:`.Client.get`. A tuple of the form (`seconds`,
            `attempts`), controlling the interval between attempts to retrieve the data
            from the URL, and the maximum number of attempts to make. Default:
            ``(30, 3)``.
        """
        # Check the message footer for a text element that is a valid URL
        url = None
        for text in getattr(message.footer, "text", []):
            if urlparse(str(text)).scheme:
                url = str(text)
                break

        if not url:
            return message

        # Unpack arguments
        wait_seconds, attempts = request.options.get("get_footer_url", (30, 3))

        # Create a temporary file to store the ZIP response
        with NamedTemporaryFile(prefix="sdmx-") as ntf:
            # Make a limited number of attempts to retrieve the file
            for a in range(attempts):
                sleep(wait_seconds)
                try:
                    # The ZIP response is stored to ntf, then used by handle_response()
                    return client.get(url=url, tofile=ntf.file)
                except requests.HTTPError:
                    if a + 1 == attempts:
                        raise
        raise RuntimeError("Maximum attempts exceeded")

    def handle_response(self, response, content):
        """Handle the polled response.

        The request for the indicated ZIP file URL returns an octet-stream;
        this handler saves it, opens it, and returns the content of the single
        contained XML file.
        """

        if response.headers["content-type"] != "application/octet-stream":
            return response, content

        # Read all the input, forcing it to be copied to
        # content.tee_filename
        while True:
            if len(content.read()) == 0:
                break

        # Open the zip archive
        with ZipFile(content.tee, mode="r") as zf:
            # The archive should contain only one file
            infolist = zf.infolist()
            assert len(infolist) == 1

            # Set the new content type
            response.headers["content-type"] = "application/xml"

            # Use the unzipped archive member as the response content
            return response, zf.open(infolist[0])
