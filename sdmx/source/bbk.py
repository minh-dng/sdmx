import logging

from sdmx.rest import Resource
from sdmx.rest.common import RequestSpec

from . import Source as BaseSource

log = logging.getLogger(__name__)


class Source(BaseSource):
    """Work around non-standard behaviour of the :ref:`BBK` web service."""

    _id = "BBK"

    def modify_request(self, request: RequestSpec) -> None:
        """Insert "/metadata" in the URL of queries for structures; omit the version."""
        if request.resource_type is not Resource.data:
            request.base_url = request.base_url + "/metadata"

            if request.version:
                log.warning(
                    f"URL part version={request.version} not supported; discarded"
                )
                request.version = None
