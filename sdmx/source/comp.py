from sdmx.rest.common import RequestSpec
from sdmx.source import Source as BaseSource

from .estat import handle_references_param


class Source(BaseSource):
    """Handle `COMP` quirks.

    .. versionadded:: 2.13.2
    """

    _id = "COMP"

    def modify_request(self, request: RequestSpec) -> None:
        """Handle the ``references`` query parameter.

        See also
        --------
        :func:`.handle_references_param`, :issue:`162`
        """
        handle_references_param(request)
