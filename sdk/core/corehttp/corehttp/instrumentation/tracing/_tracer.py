# ------------------------------------
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# ------------------------------------
from __future__ import annotations
from collections.abc import Callable
from typing import Optional, Any, TYPE_CHECKING

from ._models import Attributes

if TYPE_CHECKING:
    from ...rest import HttpRequest, HttpResponse
    try:
        from .opentelemetry_tracer import OpenTelemetryTracer
        from .opentelemetry_span import OpenTelemetrySpan
    except ImportError:
        pass


def _get_tracer_impl():
    # Check if OpenTelemetry is available/installed.
    try:
        from .opentelemetry_tracer import OpenTelemetryTracer

        return OpenTelemetryTracer
    except ImportError:
        return None


class TracingCallbackHandler:
    """A base callback handler for customizing spans in the tracing decorators and distributed tracing policy."""

    def before_method(self, span: "OpenTelemetrySpan", *args: Any, **kwargs: Any) -> None:
        """This method is called before the method is called in the tracing decorators."""

    def after_method(self, span: "OpenTelemetrySpan", result: Any) -> None:
        """This method is called after the method is called in the tracing decorators."""

    def before_http_request(self, span: "OpenTelemetrySpan", request: "HttpRequest") -> None:
        """This method is called before the HTTP request is sent in the DistributedHttpTracingPolicy."""

    def after_http_request(self, span: "OpenTelemetrySpan", response: "HttpResponse") -> None:
        """This method is called after the HTTP request is sent in the DistributedHttpTracingPolicy."""


class TracerProvider:
    """A provider for a tracer instance.

    Various metadata can be set on the provider to be used in the tracer.

    :keyword library_name: The name of the library to use in the tracer.
    :paramtype library_name: str
    :keyword library_version: The version of the library to use in the tracer.
    :paramtype library_version: str
    :keyword schema_url: Specifies the Schema URL of the emitted spans.
    :paramtype schema_url: str
    :keyword attributes: Attributes to add to the emitted spans.
    :paramtype attributes: Mapping[str, Any]
    """

    def __init__(
        self,
        *,
        library_name: Optional[str] = None,
        library_version: Optional[str] = None,
        schema_url: Optional[str] = None,
        attributes: Optional[Attributes] = None,
    ) -> None:
        self._tracer = None
        self._library_name = library_name
        self._library_version = library_version
        self._schema_url = schema_url
        self._attributes = attributes
        self._callback_handlers = {}

        # self._pre_method_callbacks = {}
        # self._post_method_callbacks = {}
        # self._pre_http_request_callbacks = {}
        # self._post_http_request_callbacks = {}

    def get_tracer(self) -> Optional["OpenTelemetryTracer"]:
        """Get the OpenTelemetry tracer instance if available.

        If OpenTelemetry is not available, this method will return None. If the tracer instance has not been created
        yet, it will be created and returned. Otherwise, the existing tracer instance will be returned.

        :return: The OpenTelemetry tracer instance if available.
        :rtype: Optional[~corehttp.instrumentation.tracing.opentelemetry_tracer.OpenTelemetryTracer]
        """
        if self._tracer is None:
            tracer_impl = _get_tracer_impl()
            if tracer_impl:
                self._tracer = tracer_impl(
                    library_name=self._library_name,
                    library_version=self._library_version,
                    schema_url=self._schema_url,
                    attributes=self._attributes,
                )
        return self._tracer


    def add_callback_handler(self, method_qualified_name: str, handler: TracingCallbackHandler) -> None:
        """Register a callback handler for the specified method."""
        self._callback_handlers[method_qualified_name] = handler

    def get_callback_handler(self, method_qualified_name: str) -> Optional[TracingCallbackHandler]:
        """Get the callback handler for the specified method."""
        return self._callback_handlers.get(method_qualified_name)

    # def add_pre_method_callback(self, method_qualified_name: str, callback: Callable) -> None:
    #     """Register a callback to be called before a method is traced."""
    #     self._pre_method_callbacks[method_qualified_name] = callback

    # def add_post_method_callback(self, method_qualified_name: str, callback: Callable) -> None:
    #     """Register a callback to be called after a method is traced."""
    #     self._post_method_callbacks[method_qualified_name] = callback

    # def get_pre_method_callback(self, method_qualified_name: str) -> Optional[Callable]:
    #     """Get the pre-method callback for the specified method."""
    #     return self._pre_method_callbacks.get(method_qualified_name)

    # def get_post_method_callback(self, method_qualified_name: str) -> Optional[Callable]:
    #     """Get the post-method callback for the specified method."""
    #     return self._post_method_callbacks.get(method_qualified_name)

    # def add_post_http_request_callback(self, method_qualified_name: str, callback: Callable) -> None:
    #     """Register a callback to be called after an HTTP request is traced."""
    #     self._post_http_request_callbacks[method_qualified_name] = callback

    # def get_post_http_request_callback(self, method_qualified_name: str) -> Optional[Callable]:
    #     """Get the post-http-request callback for the specified method."""
    #     return self._post_http_request_callbacks.get(method_qualified_name)


default_tracer_provider = TracerProvider()
"""The global tracer provider that is used by default.

:type default_tracer_provider: TracerProvider
"""
