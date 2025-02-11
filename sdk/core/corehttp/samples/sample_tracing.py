# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------
"""
FILE: sample_tracing.py

DESCRIPTION:
    This sample demonstrates how to trace a client method.

    Note: This sample requires the opentelemetry-sdk library to be installed.

USAGE:
    python sample_tracing.py
"""
from typing import Iterable, Union, Any, TYPE_CHECKING
from functools import partial

from corehttp.instrumentation.tracing import distributed_trace as core_distributed_trace
from corehttp.runtime import PipelineClient
from corehttp.rest import HttpRequest, HttpResponse
from corehttp.runtime.policies import (
    HTTPPolicy,
    SansIOHTTPPolicy,
    HeadersPolicy,
    UserAgentPolicy,
    RetryPolicy,
    DistributedHttpTracingPolicy,
)
from corehttp.settings import settings
from corehttp.instrumentation.tracing import TracerProvider as SDKTracerProvider, TracingCallbackHandler

if TYPE_CHECKING:
    from corehttp.instrumentation.tracing.opentelemetry_span import OpenTelemetrySpan
    from corehttp.rest import HttpRequest, HttpResponse

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor


tracer_provider = TracerProvider()
exporter = ConsoleSpanExporter()
span_processor = SimpleSpanProcessor(exporter)

trace.set_tracer_provider(tracer_provider)
tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
tracer = trace.get_tracer(__name__)


sdk_tracer_provider = SDKTracerProvider(
    library_name="mylibrary",
    library_version="1.0.0",
    schema_url="https://opentelemetry.io/schemas/1.23.1",
    attributes={"namespace": "Sample.Namespace"},
)

distributed_trace = partial(core_distributed_trace, tracer_provider=sdk_tracer_provider)


def sample_pre_method_hook(span, *args, **kwargs):
    print('---in sample_pre_method_hook---')
    span.set_attribute("pre-hook-attribute", "pre-hook-value")

def sample_post_method_hook(span, response):
    print('---in sample_post_method_hook---')
    span.set_attribute("post-hook-attribute", response.status_code)

def sample_post_http_request_hook(span, response):
    print('---in sample_post_http_request_hook---')
    span.set_attribute("post-http-request-hook-attribute", response.status_code)


# sdk_tracer_provider.add_pre_method_callback("SampleClient.sample_method_with_hooks", sample_pre_method_hook)
# sdk_tracer_provider.add_post_method_callback("SampleClient.sample_method_with_hooks", sample_post_method_hook)
# sdk_tracer_provider.add_post_http_request_callback("SampleClient.sample_method_with_hooks", sample_post_http_request_hook)

# class TracingCallbackHandler:

#     def before_method(self, span: "OpenTelemetrySpan", *args: Any, **kwargs: Any) -> None:
#         """This method is called before the method is called in the tracing decorator."""

#     def after_method(self, span: "OpenTelemetrySpan", result: Any) -> None:
#         """This method is called after the method is called in the tracing decorator."""

#     def before_http_request(self, span: "OpenTelemetrySpan", request: "HttpRequest") -> None:
#         """This method is called before the HTTP request is sent in the DistributedHttpTracingPolicy."""

#     def after_http_request(self, span: "OpenTelemetrySpan", response: "HttpResponse") -> None:
#         """This method is called after the HTTP request is sent in the DistributedHttpTracingPolicy."""


class SampleCallbackHandler(TracingCallbackHandler):

    def before_method(self, span: "OpenTelemetrySpan", *args: Any, **kwargs: Any) -> None:
        print('---in SampleCallbackHandler.before_method---')
        span.set_attribute("callback-handler-before-method-attribute", "ok")

    def after_method(self, span: "OpenTelemetrySpan", result: Any) -> None:
        print('---in SampleCallbackHandler.after_method---')
        span.set_attribute("callback-handler-after-method-attribute", "ok")

    def after_http_request(self, span: "OpenTelemetrySpan", response: HttpResponse) -> None:
        print('---in SampleCallbackHandler.after_http_request---')
        span.set_attribute("callback-handler-after-http-request-attribute", response.status_code)

sdk_tracer_provider.add_callback_handler("SampleClient.sample_method_with_callback_handler", SampleCallbackHandler())


class SampleClient:

    def __init__(self, endpoint: str) -> None:
        policies: Iterable[Union[HTTPPolicy, SansIOHTTPPolicy]] = [
            HeadersPolicy(),
            UserAgentPolicy("myuseragent"),
            RetryPolicy(),
            DistributedHttpTracingPolicy(tracer_provider=sdk_tracer_provider),
        ]

        self._client: PipelineClient[HttpRequest, HttpResponse] = PipelineClient(endpoint, policies=policies)

    @distributed_trace()
    def sample_method(self, **kwargs: Any) -> HttpResponse:
        request = HttpRequest("GET", "https://bing.com")
        response = self._client.send_request(request, **kwargs)
        return response

    @distributed_trace()
    def sample_method_with_callback_handler(self, **kwargs: Any) -> HttpResponse:
        request = HttpRequest("GET", "https://bing.com")
        response = self._client.send_request(request, **kwargs)
        return response

    @distributed_trace()
    def sample_method_with_hooks(self, **kwargs: Any) -> HttpResponse:
        request = HttpRequest("GET", "https://bing.com")
        response = self._client.send_request(request, **kwargs)
        return response

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SampleClient":
        self._client.__enter__()
        return self

    def __exit__(self, *exc_details: Any) -> None:
        self._client.__exit__(*exc_details)


def sample_basic():
    settings.tracing_enabled = True

    endpoint = "https://bing.com"
    with SampleClient(endpoint) as client:
        with tracer.start_as_current_span(name="MyApplication"):  # type: ignore
            response = client.sample_method(tracing_options={"attributes": {"custom_key": "custom_value"}})
            print(f"Response: {response}")


def sample_with_pre_and_post_hooks():
    settings.tracing_enabled = True

    endpoint = "https://bing.com"
    with SampleClient(endpoint) as client:
        with tracer.start_as_current_span(name="MyApplication"):  # type: ignore
            response = client.sample_method_with_hooks(
                tracing_options={"attributes": {"custom_key": "custom_value"}},
            )
            print(f"Response: {response}")


def sample_with_callback_handler():
    settings.tracing_enabled = True

    endpoint = "https://bing.com"
    with SampleClient(endpoint) as client:
        with tracer.start_as_current_span(name="MyApplication"):  # type: ignore
            response = client.sample_method_with_callback_handler(
                tracing_options={"attributes": {"custom_key": "custom_value"}},
            )
            print(f"Response: {response}")


if __name__ == "__main__":
    # sample_with_pre_and_post_hooks()
    sample_with_callback_handler()

