import re

from requests.structures import CaseInsensitiveDict

from sdmx.rest import Resource

from . import DataContentType
from . import Source as BaseSource

re_500 = re.compile(r"(An error has occurred)\.")


class Source(BaseSource):
    _id = "ABS"

    def modify_request_args(self, kwargs):
        """Select the requested ABS data format; request metadata as SDMX-ML."""
        resource_type = kwargs.get("resource_type")
        data_format = kwargs.pop("format", self.data_content_type)
        headers = CaseInsensitiveDict(kwargs.get("headers", {}))
        kwargs["headers"] = headers

        if resource_type is Resource.data:
            if data_format is DataContentType.JSON:
                headers.setdefault("Accept", "application/json")
            elif data_format is not DataContentType.XML:
                raise ValueError("ABS format must be DataContentType.JSON or XML")

        super().modify_request_args(kwargs)

        if resource_type is not None:
            headers.setdefault("Accept", "application/xml")

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
