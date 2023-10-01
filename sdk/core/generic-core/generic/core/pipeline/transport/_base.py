# --------------------------------------------------------------------------
#
# Copyright (c) Microsoft Corporation. All rights reserved.
#
# The MIT License (MIT)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the ""Software""), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED *AS IS*, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.
#
# --------------------------------------------------------------------------
from __future__ import annotations
import abc
import logging
import time
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from typing import (
    Generic,
    TypeVar,
    Union,
    Any,
    Optional,
    Dict,
    ContextManager,
    TYPE_CHECKING,
)

HTTPResponseType = TypeVar("HTTPResponseType")
HTTPRequestType = TypeVar("HTTPRequestType")
DataType = Union[bytes, str, Dict[str, Union[str, int]]]

if TYPE_CHECKING:
    from ...rest import HttpRequest

_LOGGER = logging.getLogger(__name__)

binary_type = str


def _format_url_section(template, **kwargs):
    """String format the template with the kwargs, auto-skip sections of the template that are NOT in the kwargs.

    By default in Python, "format" will raise a KeyError if a template element is not found. Here the section between
    the slashes will be removed from the template instead.

    This is used for API like Storage, where when Swagger has template section not defined as parameter.

    :param str template: a string template to fill
    :keyword dict[str,str] kwargs: Template values as string
    :rtype: str
    :returns: Template completed
    """
    last_template = template
    components = template.split("/")
    while components:
        try:
            return template.format(**kwargs)
        except KeyError as key:
            formatted_components = template.split("/")
            components = [c for c in formatted_components if "{{{}}}".format(key.args[0]) not in c]
            template = "/".join(components)
            if last_template == template:
                raise ValueError(
                    f"The value provided for the url part '{template}' was incorrect, and resulted in an invalid url"
                ) from key
            last_template = template


def _urljoin(base_url: str, stub_url: str) -> str:
    """Append to end of base URL without losing query parameters.

    :param str base_url: The base URL.
    :param str stub_url: Section to append to the end of the URL path.
    :returns: The updated URL.
    :rtype: str
    """
    parsed_base_url = urlparse(base_url)

    # Can't use "urlparse" on a partial url, we get incorrect parsing for things like
    # document:build?format=html&api-version=2019-05-01
    split_url = stub_url.split("?", 1)
    stub_url_path = split_url.pop(0)
    stub_url_query = split_url.pop() if split_url else None

    # Note that _replace is a public API named that way to avoid to avoid conflicts in namedtuple
    # https://docs.python.org/3/library/collections.html?highlight=namedtuple#collections.namedtuple
    parsed_base_url = parsed_base_url._replace(
        path=parsed_base_url.path.rstrip("/") + "/" + stub_url_path,
    )
    if stub_url_query:
        query_params = [stub_url_query]
        if parsed_base_url.query:
            query_params.insert(0, parsed_base_url.query)
        parsed_base_url = parsed_base_url._replace(query="&".join(query_params))
    return parsed_base_url.geturl()


class HttpTransport(ContextManager["HttpTransport"], abc.ABC, Generic[HTTPRequestType, HTTPResponseType]):
    """An http sender ABC."""

    @abc.abstractmethod
    def send(self, request: HTTPRequestType, **kwargs: Any) -> HTTPResponseType:
        """Send the request using this HTTP sender.

        :param request: The pipeline request object
        :type request: ~generic.core.rest.HTTPRequest
        :return: The pipeline response object.
        :rtype: ~generic.core.rest.HttpResponse
        """

    @abc.abstractmethod
    def open(self) -> None:
        """Assign new session if one does not already exist."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the session if it is not externally owned."""

    def sleep(self, duration: float) -> None:
        """Sleep for the specified duration.

        You should always ask the transport to sleep, and not call directly
        the stdlib. This is mostly important in async, as the transport
        may not use asyncio but other implementations like trio and they have their own
        way to sleep, but to keep design
        consistent, it's cleaner to always ask the transport to sleep and let the transport
        implementor decide how to do it.

        :param float duration: The number of seconds to sleep.
        """
        time.sleep(duration)


class PipelineClientBase:
    """Base class for pipeline clients.

    :param str base_url: URL for the request.
    """

    def __init__(self, base_url: str):
        self._base_url = base_url

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, str]],
        headers: Optional[Dict[str, str]],
        content: Any,
        form_content: Optional[Dict[str, Any]],
        stream_content: Any,
    ) -> HttpRequest:
        """Create HttpRequest object.

        If content is not None, guesses will be used to set the right body:
        - If content is an XML tree, will serialize as XML
        - If content-type starts by "text/", set the content as text
        - Else, try JSON serialization

        :param str method: HTTP method (GET, HEAD, etc.)
        :param str url: URL for the request.
        :param dict params: URL query parameters.
        :param dict headers: Headers
        :param content: The body content
        :type content: bytes or str or dict
        :param dict form_content: Form content
        :param stream_content: The body content as a stream
        :type stream_content: stream or generator or asyncgenerator
        :return: An HttpRequest object
        :rtype: ~generic.core.rest.HttpRequest
        """
        request = HttpRequest(
            method,
            self.format_url(url),
            params=params,
            headers=headers,
            content=content,
        )

        req_content, req_json, req_data, req_files = None, None, None, None
        content_type = headers.pop("Content-Type", None) if headers else None

        if content is not None:
            if isinstance(content, ET.Element):
                req_content = content
            # https://github.com/Azure/azure-sdk-for-python/issues/12137
            # A string is valid JSON, make the difference between text
            # and a plain JSON string.
            # Content-Type is a good indicator of intent from user
            elif content_type and content_type.startswith("text/"):
                req_content = content
            else:
                # Assume json
                req_json = content

        if form_content:
            if content_type and content_type.lower() == "application/x-www-form-urlencoded":
                req_data = form_content
            else:  # Assume "multipart/form-data"
                req_files = form_content
        elif stream_content:
            req_content = stream_content

        request = HttpRequest(
            method,
            self.format_url(url),
            params=params,
            headers=headers,
            # content=req_content,
            # json=req_json,
            # data=req_data,
            # files=req_files,
        )

        return request

    def format_url(self, url_template: str, **kwargs: Any) -> str:
        """Format request URL with the client base URL, unless the
        supplied URL is already absolute.

        Note that both the base url and the template url can contain query parameters.

        :param str url_template: The request URL to be formatted if necessary.
        :rtype: str
        :return: The formatted URL.
        """
        url = _format_url_section(url_template, **kwargs)
        if url:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                url = url.lstrip("/")
                try:
                    base = self._base_url.format(**kwargs).rstrip("/")
                except KeyError as key:
                    err_msg = "The value provided for the url part {} was incorrect, and resulted in an invalid url"
                    raise ValueError(err_msg.format(key.args[0])) from key

                url = _urljoin(base, url)
        else:
            url = self._base_url.format(**kwargs)
        return url

    def get(
        self,
        url: str,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        content: Any = None,
        form_content: Optional[Dict[str, Any]] = None,
    ) -> HttpRequest:
        """Create a GET request object.

        :param str url: The request URL.
        :param dict params: Request URL parameters.
        :param dict headers: Headers
        :param content: The body content
        :type content: bytes or str or dict
        :param dict form_content: Form content
        :return: An HttpRequest object
        :rtype: ~generic.core.rest.HttpRequest
        """
        request = self._request("GET", url, params, headers, content, form_content, None)
        request.method = "GET"
        return request

    def put(
        self,
        url: str,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        content: Any = None,
        form_content: Optional[Dict[str, Any]] = None,
        stream_content: Any = None,
    ) -> HttpRequest:
        """Create a PUT request object.

        :param str url: The request URL.
        :param dict params: Request URL parameters.
        :param dict headers: Headers
        :param content: The body content
        :type content: bytes or str or dict
        :param dict form_content: Form content
        :param stream_content: The body content as a stream
        :type stream_content: stream or generator or asyncgenerator
        :return: An HttpRequest object
        :rtype: ~generic.core.rest.HttpRequest
        """
        request = self._request("PUT", url, params, headers, content, form_content, stream_content)
        return request

    def post(
        self,
        url: str,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        content: Any = None,
        form_content: Optional[Dict[str, Any]] = None,
        stream_content: Any = None,
    ) -> HttpRequest:
        """Create a POST request object.

        :param str url: The request URL.
        :param dict params: Request URL parameters.
        :param dict headers: Headers
        :param content: The body content
        :type content: bytes or str or dict
        :param dict form_content: Form content
        :param stream_content: The body content as a stream
        :type stream_content: stream or generator or asyncgenerator
        :return: An HttpRequest object
        :rtype: ~generic.core.rest.HttpRequest
        """
        request = self._request("POST", url, params, headers, content, form_content, stream_content)
        return request

    def head(
        self,
        url: str,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        content: Any = None,
        form_content: Optional[Dict[str, Any]] = None,
        stream_content: Any = None,
    ) -> HttpRequest:
        """Create a HEAD request object.

        :param str url: The request URL.
        :param dict params: Request URL parameters.
        :param dict headers: Headers
        :param content: The body content
        :type content: bytes or str or dict
        :param dict form_content: Form content
        :param stream_content: The body content as a stream
        :type stream_content: stream or generator or asyncgenerator
        :return: An HttpRequest object
        :rtype: ~generic.core.rest.HttpRequest
        """
        request = self._request("HEAD", url, params, headers, content, form_content, stream_content)
        return request

    def patch(
        self,
        url: str,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        content: Any = None,
        form_content: Optional[Dict[str, Any]] = None,
        stream_content: Any = None,
    ) -> HttpRequest:
        """Create a PATCH request object.

        :param str url: The request URL.
        :param dict params: Request URL parameters.
        :param dict headers: Headers
        :param content: The body content
        :type content: bytes or str or dict
        :param dict form_content: Form content
        :param stream_content: The body content as a stream
        :type stream_content: stream or generator or asyncgenerator
        :return: An HttpRequest object
        :rtype: ~generic.core.rest.HttpRequest
        """
        request = self._request("PATCH", url, params, headers, content, form_content, stream_content)
        return request

    def delete(
        self,
        url: str,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        content: Any = None,
        form_content: Optional[Dict[str, Any]] = None,
    ) -> HttpRequest:
        """Create a DELETE request object.

        :param str url: The request URL.
        :param dict params: Request URL parameters.
        :param dict headers: Headers
        :param content: The body content
        :type content: bytes or str or dict
        :param dict form_content: Form content
        :return: An HttpRequest object
        :rtype: ~generic.core.rest.HttpRequest
        """
        request = self._request("DELETE", url, params, headers, content, form_content, None)
        return request

    def merge(
        self,
        url: str,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        content: Any = None,
        form_content: Optional[Dict[str, Any]] = None,
    ) -> HttpRequest:
        """Create a MERGE request object.

        :param str url: The request URL.
        :param dict params: Request URL parameters.
        :param dict headers: Headers
        :param content: The body content
        :type content: bytes or str or dict
        :param dict form_content: Form content
        :return: An HttpRequest object
        :rtype: ~generic.core.rest.HttpRequest
        """
        request = self._request("MERGE", url, params, headers, content, form_content, None)
        return request

    def options(
        self, url: str, params: Optional[Dict[str, str]] = None, headers: Optional[Dict[str, str]] = None, **kwargs: Any
    ) -> HttpRequest:
        """Create a OPTIONS request object.

        :param str url: The request URL.
        :param dict params: Request URL parameters.
        :param dict headers: Headers
        :keyword content: The body content
        :type content: bytes or str or dict
        :keyword dict form_content: Form content
        :return: An HttpRequest object
        :rtype: ~generic.core.rest.HttpRequest
        """
        content = kwargs.get("content")
        form_content = kwargs.get("form_content")
        request = self._request("OPTIONS", url, params, headers, content, form_content, None)
        return request
