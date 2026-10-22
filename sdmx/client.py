import logging
from functools import partial
from typing import IO, TYPE_CHECKING, Any, Literal, overload
from warnings import warn

import requests
from requests.structures import CaseInsensitiveDict

from sdmx.format import Version
from sdmx.model import common
from sdmx.model.v21 import DataStructureDefinition
from sdmx.reader import get_first_reader
from sdmx.rest import Resource
from sdmx.rest.common import RequestSpec
from sdmx.session import DEFAULT_TIMEOUT, ResponseIO, Session
from sdmx.source import DataContentType, NoSource, get_source, list_sources

from . import message

if TYPE_CHECKING:
    import io
    import os

    import sdmx.message
    import sdmx.source

log = logging.getLogger(__name__)

#: Value of the ``Accept`` header to request structure-specific data.
STRUCTURE_SPECIFIC_DATA = "application/vnd.sdmx.structurespecificdata+xml;version=2.1"


def Request(*args, **kwargs) -> "Client":
    """Compatibility function for :class:`Client`.

    .. versionadded:: 2.0

    .. deprecated:: 2.0
       Will be removed in :mod:`sdmx` version 3.0.

    """
    message = "Request class will be removed in v3.0; use Client(…)"
    log.warning(message)
    warn(message, DeprecationWarning)
    return Client(*args, **kwargs)


class Client:
    """Client for a SDMX REST web service.

    Parameters
    ----------
    source : str or source.Source
        Identifier of a data source. If a string, must be one of the known sources in
        :meth:`list_sources`.
    session :
        :class:`.requests.Session` instance. If not supplied, an instance of
        :class:`.Session` is created. If a plain :class:`requests.Session` is supplied,
        a default timeout of 30 seconds is applied to every request; set
        :py:`session.timeout = None` to allow requests to wait indefinitely.
    log_level : int
        Override the package-wide logger with one of the
        :ref:`standard logging levels <py:levels>`.

        .. deprecated:: 2.0
           Will be removed in :mod:`sdmx` version 3.0.
    **session_opts
        Additional keyword arguments are passed to :class:`.Session` and thus to
        :class:`.requests_cache.CachedSession` and its backend classes (if installed).
    """

    cache: dict[str, "sdmx.message.Message"] = {}

    #: :class:`.source.Source` for requests sent from the instance.
    source: "sdmx.source.Source"

    #: :class:`.Session` for queries sent from the instance.
    session: requests.Session

    def __init__(
        self,
        source=None,
        *,
        session: "requests.Session | None" = None,
        log_level=None,
        **session_opts,
    ):
        try:
            self.source = get_source(source) if source else NoSource
        except KeyError:
            raise ValueError(
                f"source must be None or one of: {' '.join(list_sources())}"
            )

        if session:
            if session_opts:
                raise ValueError("Client(…, session=…) with additional keyword args")
            self.session = session
        else:
            # Create an HTTP Session object to reuse a connection for multiple requests
            self.session = Session(**session_opts)

        if log_level:
            message = "Client(…, log_level=…) parameter"
            log.warning(f"Deprecated: {message}")
            warn(message, DeprecationWarning)
            logging.getLogger("pandasdmx").setLevel(log_level)

    def __getattr__(self, name):
        """Convenience methods."""
        try:
            # Provide resource_type as a positional argument, so that the first
            # positional argument to the convenience method is treated as resource_id
            func = partial(self.get, Resource[name])
        except KeyError:
            raise AttributeError
        else:
            # Modify the docstring to explain the argument fixed by the convenience
            # method
            func.__doc__ = self.get.__doc__.replace(
                ".\n", f" with resource_type={repr(name)}.\n", 1
            )
            return func

    def __dir__(self):
        """Include convenience methods in dir()."""
        return super().__dir__() + [ep.name for ep in Resource]

    def clear_cache(self):
        self.cache.clear()

    @property
    def timeout(self):
        warn(
            "Getting Client.timeout directly; use Client.session.timeout",
            DeprecationWarning,
        )
        return self.session.timeout

    @timeout.setter
    def timeout(self, value):
        warn(
            f"Setting Client.timeout directly; use Client.session.timeout={value}",
            DeprecationWarning,
        )
        self.session.timeout = value

    def series_keys(self, flow_id, use_cache=True) -> list[common.SeriesKey]:
        """Return all :class:`.SeriesKey` for *flow_id*.

        Returns
        -------
        list
        """
        # download an empty dataset with all available series keys
        return (
            self.data(flow_id, params={"detail": "serieskeysonly"}, use_cache=use_cache)
            .data[0]
            .series.keys()
        )

    def _make_key(
        self, resource_type, resource_id, key, dsd
    ) -> tuple[str, DataStructureDefinition]:
        """Validate `key` if possible.

        If key is :class:`dict`, validate items against `dsd` and construct a query
        string which becomes part of the URL. Otherwise, do nothing, as `key` must be a
        :class:`str` confirming to the REST API spec.
        """
        if not (
            resource_type in (Resource.data, Resource.availableconstraint)
            and isinstance(key, dict)
        ):
            return key, dsd

        # Select validation method based on agency capabilities
        if dsd:
            # DSD was provided
            pass
        elif self.source.supports[Resource.datastructure]:
            # Retrieve the DataStructureDefinition
            dsd = (
                self.dataflow(
                    resource_id, params=dict(references="all"), use_cache=True
                )
                .dataflow[resource_id]
                .structure
            )

            if dsd.is_external_reference:
                # DataStructureDefinition was not retrieved with the Dataflow query;
                # retrieve it explicitly
                msg = self.get(resource=dsd, use_cache=True)
                assert isinstance(msg, message.StructureMessage)
                dsd = msg.structure[dsd.id]
        else:
            # Construct a DSD from the keys
            dsd = DataStructureDefinition.from_keys(self.series_keys(resource_id))

        # Make a ContentConstraint from the key
        cc = dsd.make_constraint(key)

        return cc.to_query_string(dsd), dsd

    def _request_spec(self, kwargs, options) -> RequestSpec:
        """Describe the query for the arguments of :meth:`get`."""
        resource_type = kwargs.pop("resource_type", None)
        resource_id = kwargs.pop("resource_id", None)
        key = kwargs.pop("key", None)
        dsd = kwargs.pop("dsd", None)
        structure_specific = (
            isinstance(dsd, DataStructureDefinition)
            and self.source.data_content_type is DataContentType.XML
        )

        if isinstance(key, dict):
            # Make the key, and retain the DSD (if any) for use in parsing
            key, dsd = self._make_key(resource_type, resource_id, key, dsd)
        elif not (key is None or isinstance(key, str)):
            raise TypeError(f"key must be str or dict; got {key.__class__.__name__}")

        params = dict(kwargs.pop("params", None) or {})
        headers = CaseInsensitiveDict(kwargs.pop("headers", None) or {})
        agency_id = kwargs.pop("agency_id", None)
        version = kwargs.pop("version", None)

        # Other arguments are parameters of the URL, which will raise if it cannot
        # handle them
        if overlap := set(kwargs) & set(params):
            raise ValueError(f"Duplicate values for query parameters {overlap}")
        params.update(kwargs)

        return RequestSpec(
            resource_type,
            resource_id,
            agency_id,
            version,
            key,
            params,
            headers,
            dsd,
            structure_specific,
            options,
            self.source.url,
        )

    def _request_from_spec(self, request: RequestSpec) -> "requests.Request":
        """Complete `request` with defaults of the source and prepare a request."""
        assert request.resource_type is not None
        rt = request.resource_type
        # SDMX-REST 2.1 data and availability URLs have no agency; 3.0.0 ones do
        in_url = rt not in (Resource.data, Resource.availableconstraint) or (
            Version["2.1"] not in self.source.versions
        )
        if request.agency_id is None and in_url:
            request.agency_id = self.source.default_agency

        if request.structure_specific:
            request.headers.setdefault("Accept", STRUCTURE_SPECIFIC_DATA)

        # Headers of the source: for all resource types ("*"), and for this one
        defaults = {
            **self.source.headers.get("*", {}),
            **self.source.headers.get(rt.name, {}),
        }
        for name, value in defaults.items():
            request.headers.setdefault(name, value)

        # Identify the URL class; handle the `request`
        # TODO specify API version for sources that support multiple API versions at the
        #      same URL
        url = self.source.get_url_class()(
            self.source,
            rt,
            resource_id=request.resource_id,
            **{
                name: value
                for name in ("agency_id", "version", "key")
                if (value := getattr(request, name)) is not None
            },
            params=request.params,
        )

        # Allow requests to keep track of the query parameters and rest of the URL
        return requests.Request(
            "get",
            url.join(with_query=False, base_url=request.base_url),
            params=url.query,
            headers=request.headers,
        )

    def _request_from_url(self, kwargs) -> "requests.Request":
        url = kwargs.pop("url")
        parameters = kwargs.pop("params", {})
        headers = kwargs.pop("headers", {})

        # kwargs with values other than None are an error
        extra_args = dict(filter(lambda i: i[1] is not None, kwargs.items()))
        if len(extra_args):
            raise ValueError(f"{repr(extra_args)} supplied with get(url=...)")

        return requests.Request("get", url, params=parameters, headers=headers)

    def _handle_get_kwargs(self, kwargs) -> tuple[dict, dict]:  # noqa: C901 TODO reduce complexity 12 → ≤10
        if "provider" in kwargs:
            raise TypeError("provider= was removed in 3.0.0; use agency_id=")

        if kwargs.pop("validate", None) is not None:
            warn("validate= keyword argument to Client.get()", DeprecationWarning)

        # "resource_type", if given, should be a member of the Resource enum
        if resource_type := kwargs.pop("resource_type", None):
            try:
                resource_type = Resource[resource_type]
            except KeyError:
                raise KeyError(
                    f"resource_type {resource_type!r} not in {Resource.describe()}"
                ) from None
            else:
                kwargs.update(resource_type=resource_type)

        # "resource", if an object is given, should satisfy certain conditions
        if resource := kwargs.pop("resource", None):
            assert isinstance(resource, common.MaintainableArtefact)

            # Class of the object
            resource_type = kwargs.setdefault(
                "resource_type", Resource.from_obj(resource)
            )
            assert kwargs["resource_type"] == Resource.from_obj(resource)

            # ID of the object
            kwargs.update(resource_id=kwargs.get("resource_id") or resource.id)
            assert kwargs["resource_id"] == resource.id, (
                f"mismatch between resource_id={kwargs['resource_id']!r} and "
                f"resource={resource!r}"
            )

        force = kwargs.pop("force", False)
        if resource_type and not (self.source.supports[resource_type] or force):
            raise NotImplementedError(
                f"{self.source.id} does not implement or support the "
                f"{resource_type!r} API endpoint. Use force=True to override"
            )

        def _collect(*keywords) -> dict[str, Any]:
            return {kw: kwargs.pop(kw) for kw in keywords if kw in kwargs}

        # Update session attributes. These changes persist.
        for name, value in _collect("cert", "proxies", "stream", "verify").items():
            # Log if the new value is different from the old
            old_value = getattr(self.session, name)
            if value != old_value:
                log.debug(f"Client.session.{name}={value} replaces {old_value}")

            # Store
            setattr(self.session, name, value)

        # Separate kwargs for requests.Session.send(), applied to the current request
        # only; the Session timeout applies otherwise
        send_kwargs = _collect("allow_redirects", "timeout")

        # Return remaining kwargs, and those for .send()
        return kwargs, send_kwargs

    @overload
    def get(
        self,
        resource_type: str | Resource | None = None,
        resource_id: str | None = None,
        tofile: "os.PathLike | IO | None" = None,
        use_cache: bool = False,
        dry_run: Literal[False] = False,
        **kwargs,
    ) -> "sdmx.message.Message": ...

    @overload
    def get(
        self,
        resource_type: str | Resource | None = None,
        resource_id: str | None = None,
        tofile: "os.PathLike | IO | None" = None,
        use_cache: bool = False,
        dry_run: Literal[True] = True,
        **kwargs,
    ) -> "requests.Request": ...

    def get(
        self,
        resource_type: str | Resource | None = None,
        resource_id: str | None = None,
        tofile: "os.PathLike | IO | None" = None,
        use_cache: bool = False,
        dry_run: bool = False,
        **kwargs,
    ) -> "sdmx.message.Message | requests.Request":
        """Retrieve SDMX data or metadata.

        (Meta)data is retrieved from the :attr:`source` of the current Client. The
        item(s) to retrieve can be specified in one of two ways:

        1. `resource_type`, `resource_id`: These give the type (see :class:`Resource`)
           and, optionally, ID of the item(s). If the `resource_id` is not given, all
           items of the given type are retrieved.
        2. a `resource` object, i.e. a :class:`.MaintainableArtefact`: `resource_type`
           and `resource_id` are determined by the object's class and
           :attr:`id <.IdentifiableArtefact.id>` attribute, respectively.

        Data is retrieved with `resource_type='data'`. In this case, the optional
        keyword argument `key` can be used to constrain the data that is retrieved.
        Examples of the formats for `key`:

        1. ``{'GEO': ['EL', 'ES', 'IE']}``: :class:`dict` with dimension name(s) mapped
           to an iterable of allowable values.
        2. ``{'GEO': 'EL+ES+IE'}``: :class:`dict` with dimension name(s) mapped to
           strings joining allowable values with `'+'`, the logical 'or' operator for
           SDMX web services.
        3. ``'....EL+ES+IE'``: :class:`str` in which ordered dimension values (some
           empty, ``''``) are joined with ``'.'``. Using this form requires knowledge
           of the dimension order in the target data `resource_id`; in the example,
           dimension 'GEO' is the fifth of five dimensions: ``'.'.join(['', '', '', '',
           'EL+ES+IE'])``. :meth:`.CubeRegion.to_query_string` can also be used to
           create properly formatted strings.

        For formats 1 and 2, but not 3, the `key` argument is validated against the
        relevant :class:`DSD <.BaseDataStructureDefinition>`, either given with the
        `dsd` keyword argument, or retrieved from the web service before the main query.

        For the optional `param` keyword argument, some useful parameters are:

        - 'startperiod', 'endperiod': restrict the time range of data to retrieve.
        - 'references': control which item(s) related to a metadata resource are
          retrieved, e.g. `references='parentsandsiblings'`.

        Parameters
        ----------
        resource_type : str or :class:`Resource`, optional
            Type of resource to retrieve.
        resource_id : str, optional
            ID of the resource to retrieve.
        tofile : str or :class:`~os.PathLike` or `file-like object`, optional
            File path or file-like to write SDMX data as it is received.
        use_cache : bool, optional
            If :obj:`True`, return a previously retrieved :class:`~.Message` from
            :attr:`cache`, or update the cache with a newly-retrieved Message.
        dry_run : bool, optional
            If :obj:`True`, prepare and return a :class:`requests.Request` object, but
            do not execute the query. The prepared URL and headers can be examined by
            inspecting the returned object.
        **kwargs
            Other, optional parameters (below).

        Other Parameters
        ----------------
        agency_id : str
            ID of the agency that maintains the resource. Default: the
            :attr:`~.Source.default_agency` of the :attr:`source`, or otherwise the ID
            of the :attr:`source`. SDMX-REST 2.1 data and availability URLs have no
            agency.

            An SDMX web service is a ‘data source’ operated by a specific, ‘source’
            agency. A web service may host data or metadata originally published by one
            or more ‘provider’ agencies. Many sources are also providers. Other
            agencies—e.g. the SDMX Global Registry—simply aggregate (meta)data from
            other providers, but do not provide any (meta)data themselves.
        allow_redirects : bool
            If :obj:`False`, do not follow HTTP redirects for the current request.
            Default: :obj:`True`, as for :meth:`requests.Session.send`.
        dsd : :class:`DataStructureDefinition <.BaseDataStructureDefinition>`
            Existing object used to validate the `key` argument. If not provided, an
            additional query executed to retrieve a DSD in order to validate the `key`.
        force : bool
            If :obj:`True`, execute the query even if the :attr:`source` does not
            support queries for the given `resource_type`. Default: :obj:`False`.
        headers : dict
            HTTP headers. Each given header replaces the header of the same name that the
            :attr:`source` would use by default.
        key : str or dict
            For queries with `resource_type='data'`. :class:`str` values are not
            validated; :class:`dict` values are validated using
            :meth:`~.DataStructureDefinition.make_constraint`.
        params : dict
            Query parameters. The `SDMX REST web service guidelines <https://\
            github.com/sdmx-twg/sdmx-rest/tree/master/v2_1/ws/rest/docs>`_
            describe parameters and allowable values for different queries. `params` is
            not validated before the query is executed.
        resource : :class:`~.MaintainableArtefact` subclass
            Object to retrieve. If given, `resource_type` and `resource_id` are ignored.
        timeout : float
            Timeout in seconds for the current request only, overriding the
            :attr:`.Session.timeout`. Default: the Session timeout; or
            :data:`.DEFAULT_TIMEOUT` for a :class:`requests.Session` without a
            `timeout` attribute.
        version : str
            :attr:`~.VersionableArtefact.version>` of a resource to retrieve. Default:
            the keyword 'latest'.

        Returns
        -------
        :class:`~.Message` or :class:`~requests.Request`
            The requested SDMX message or, if `dry_run` is :obj:`True`, the prepared
            request object.

        Raises
        ------
        NotImplementedError
            If the :attr:`source` does not support the given `resource_type` and `force`
            is not :obj:`True`.
        """
        # Insert resource_type and resource_id into kwargs
        kwargs.update(dict(resource_type=resource_type, resource_id=resource_id))

        kwargs, send_kwargs = self._handle_get_kwargs(kwargs)

        # Arguments for hooks of the source are not parameters of the URL
        options = {k: kwargs.pop(k) for k in self.source.hook_options if k in kwargs}

        # Handle arguments
        if "url" in kwargs:
            request = RequestSpec(None, options=options)
            req = self._request_from_url(kwargs)
        else:
            request = self._request_spec(kwargs, options)
            self.source.modify_request(request)
            req = self._request_from_spec(request)

        req_prepared = self.session.prepare_request(req)
        if dry_run:
            return req_prepared  # type: ignore [return-value]

        # Now get the SDMX message via HTTP
        log.info(f"Request {req_prepared.url}")
        log.info(f"with headers {req_prepared.headers}")

        # Try to get resource from memory cache if specified
        if use_cache and req_prepared.url:
            try:
                return self.cache[req_prepared.url]
            except KeyError:
                log.info("Not found in cache")
                pass

        try:
            # Send the request; the Session timeout — or the default — applies unless
            # overridden per request
            send_kwargs = {
                "timeout": getattr(self.session, "timeout", DEFAULT_TIMEOUT),
                **send_kwargs,
            }
            response = self.session.send(req_prepared, **send_kwargs)
            response.raise_for_status()
        except requests.exceptions.ConnectionError as e:
            raise e from None
        except requests.exceptions.HTTPError as e:
            # Convert a 501 response to a Python NotImplementedError
            if e.response.status_code == 501:
                raise NotImplementedError(
                    f"{resource_type!r} endpoint at {e.request.url}"
                )
            else:
                raise

        # Maybe copy the response to file as it's received
        response_content: "io.IOBase" = ResponseIO(response, tee=tofile)

        # Allow a source class to modify the response (e.g. headers) or content
        response, response_content = self.source.handle_response(
            response, response_content
        )

        # Select reader class: by the content-type header, then by the content
        try:
            Reader = get_first_reader((response, response_content))
        except ValueError:
            raise ValueError(
                "can't determine a reader for response content type "
                + repr(response.headers.get("content-type", None))
            ) from None

        # Instantiate reader
        reader = Reader()

        # Parse the message, using any provided or auto-queried DSD
        msg = reader.convert(response_content, structure=request.dsd)

        # Store the HTTP response with the message
        msg.response = response

        # Call the finish_message() hook
        msg = self.source.finish_message(msg, self, request)

        # store in memory cache if needed
        if use_cache and req_prepared.url:
            self.cache[req_prepared.url] = msg

        return msg

    def preview_data(self, flow_id, key={}) -> list[common.SeriesKey]:
        """Return a preview of data.

        For the Dataflow `flow_id`, return all series keys matching `key`. Uses a
        feature supported by some data providers that returns :class:`SeriesKeys
        <.SeriesKey>` without the corresponding
        :class:`Observations <.BaseObservation>`.

        To count the number of series::

            keys = sdmx.Client('PROVIDER').preview_data('flow')
            len(keys)

        To get a :mod:`pandas` object containing the key values::

            keys_df = sdmx.to_pandas(keys)

        Parameters
        ----------
        flow_id : str
            Dataflow to preview.
        key : dict, optional
            Mapping of `dimension` to `values`, where `values` may be a '+'-delimited
            list of values. If given, only SeriesKeys that match `key` are returned. If
            not given, preview_data is equivalent to
            ``list(client.series_keys(flow_id))``.

        Returns
        -------
        list of :class:`.SeriesKey`
        """
        # Retrieve the series keys
        all_keys = self.series_keys(flow_id)

        if len(key):
            # Construct a DSD from the keys
            dsd = DataStructureDefinition.from_keys(all_keys)

            # Make a ContentConstraint from *key*
            cc = dsd.make_constraint(key)

            # Filter the keys
            return [k for k in all_keys if k in cc]
        else:
            # No key is provided
            return list(all_keys)


def read_url(url, **kwargs) -> "sdmx.message.Message | requests.Request":
    """Request a URL directly."""
    return Client().get(url=url, **kwargs)
