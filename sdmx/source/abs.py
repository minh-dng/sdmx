import re

from sdmx.rest import Resource
from sdmx.rest.common import RequestSpec

from . import DataContentType
from . import Source as BaseSource

re_500 = re.compile(r"(An error has occurred)\.")


class Source(BaseSource):
    _id = "ABS"

    hook_options = frozenset({"format"})

    def modify_request(self, request: RequestSpec) -> None:
        """Select the requested ABS data format.

        The data format is the :py:`format=` argument to :meth:`.Client.get`, one of
        :attr:`.DataContentType.JSON` or :attr:`.DataContentType.XML`. Default: the
        :attr:`data_content_type` of the source. Metadata and XML data are
        requested as SDMX-ML, via :attr:`headers`.
        """
        if request.resource_type is Resource.data:
            data_format = request.options.get("format", self.data_content_type)
            if data_format is DataContentType.JSON:
                request.headers.setdefault("Accept", "application/json")
            elif data_format is not DataContentType.XML:
                raise ValueError("ABS format must be DataContentType.JSON or XML")
            elif not request.structure_specific:
                request.headers.setdefault("Accept", "application/xml")

    def handle_response(self, response, content):
        """Handle ABS' own text/html error page for some endpoints."""
        ctype = response.headers.get("content-type", "")
        if "text/html" in ctype:
            buf = ""
            while True:
                chunk = content.read().decode()
                if len(chunk) == 0:
                    break

                buf += chunk

                match = re_500.search(buf)
                if match:
                    # Overwrite the original response
                    (response.reason,) = match.groups()
                    response.status_code = 500
                    response.raise_for_status()

        return super().handle_response(response, content)
