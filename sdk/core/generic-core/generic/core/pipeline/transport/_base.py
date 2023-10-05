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

from typing import Generic, TypeVar, Any, ContextManager, Union, Optional, MutableMapping

HTTPResponseType = TypeVar("HTTPResponseType")
HTTPRequestType = TypeVar("HTTPRequestType")

_LOGGER = logging.getLogger(__name__)


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


def _create_connection_config(  # pylint: disable=unused-argument
    *,
    connection_timeout: float = 300,
    read_timeout: float = 300,
    connection_verify: Union[bool, str] = True,
    connection_cert: Optional[str] = None,
    connection_data_block_size: int = 4096,
    **kwargs: Any,
) -> MutableMapping[str, Any]:
    """HTTP transport connection configuration settings.

    :keyword float connection_timeout: A single float in seconds for the connection timeout. Defaults to 300 seconds.
    :keyword float read_timeout: A single float in seconds for the read timeout. Defaults to 300 seconds.
    :keyword connection_verify: SSL certificate verification. Enabled by default. Set to False to disable,
     alternatively can be set to the path to a CA_BUNDLE file or directory with certificates of trusted CAs.
    :paramtype connection_verify: bool or str
    :keyword str connection_cert: Client-side certificates. You can specify a local cert to use as client side
     certificate, as a single file (containing the private key and the certificate) or as a tuple of both files' paths.
    :keyword int connection_data_block_size: The block size of data sent over the connection. Defaults to 4096 bytes.

    :return: The connection configuration.
    :rtype: MutableMapping[str, any]
    """
    return {
        "connection_timeout": connection_timeout,
        "read_timeout": read_timeout,
        "connection_verify": connection_verify,
        "connection_cert": connection_cert,
        "data_block_size": connection_data_block_size,
    }


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
