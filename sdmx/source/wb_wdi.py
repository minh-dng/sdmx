from sdmx.rest import Resource
from sdmx.rest.common import RequestSpec

from . import Source as BaseSource


class Source(BaseSource):
    _id = "WB_WDI"

    def modify_request(self, request: RequestSpec) -> None:
        """Handle quirks of the World Bank WDI web service."""
        if request.resource_type is Resource.categoryscheme:
            # Service does not respond to requests for "WB" category schemes
            request.agency_id = request.agency_id or "all"

        if isinstance(request.key, str):
            # Data queries fail without a trailing slash
            request.key += "/"
