# coding=utf-8
# --------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------
from __future__ import absolute_import

from io import BytesIO
from email.message import Message
from email.policy import HTTP
import os
from typing import (
    TYPE_CHECKING,
    cast,
    IO,
    Union,
    Tuple,
    Optional,
    Sequence,
)
from http.client import HTTPConnection
from urllib.parse import urlparse

if TYPE_CHECKING:
    # importing both the py3 RestHttpRequest and the fallback RestHttpRequest
    from ..rest._rest_py3 import HttpRequest as RestHttpRequestPy3
    from ..rest._aiohttp import RestAioHttpTransportResponse

    HTTPRequestType = RestHttpRequestPy3


binary_type = str


class BytesIOSocket:
    """Mocking the "makefile" of socket for HTTPResponse.
    This can be used to create a http.client.HTTPResponse object
    based on bytes and not a real socket.

    :param bytes bytes_data: The bytes to use to mock the socket.
    """

    def __init__(self, bytes_data):
        self.bytes_data = bytes_data

    def makefile(self, *_):
        return BytesIO(self.bytes_data)


def _format_parameters_helper(http_request, params):
    """Helper for format_parameters.

    Format parameters into a valid query string.
    It's assumed all parameters have already been quoted as
    valid URL strings.

    :param http_request: The http request whose parameters
     we are trying to format
    :type http_request: any
    :param dict params: A dictionary of parameters.
    """
    query = urlparse(http_request.url).query
    if query:
        http_request.url = http_request.url.partition("?")[0]
        existing_params = {p[0]: p[-1] for p in [p.partition("=") for p in query.split("&")]}
        params.update(existing_params)
    query_params = []
    for k, v in params.items():
        if isinstance(v, list):
            for w in v:
                if w is None:
                    raise ValueError("Query parameter {} cannot be None".format(k))
                query_params.append("{}={}".format(k, w))
        else:
            if v is None:
                raise ValueError("Query parameter {} cannot be None".format(k))
            query_params.append("{}={}".format(k, v))
    query = "?" + "&".join(query_params)
    http_request.url = http_request.url + query


def _pad_attr_name(attr: str, backcompat_attrs: Sequence[str]) -> str:
    """Pad hidden attributes so users can access them.

    Currently, for our backcompat attributes, we define them
    as private, so they're hidden from intellisense and sphinx,
    but still allow users to access them as public attributes
    for backcompat purposes. This function is called so if
    users access publicly call a private backcompat attribute,
    we can return them the private variable in getattr

    :param str attr: The attribute name
    :param list[str] backcompat_attrs: The list of backcompat attributes
    :rtype: str
    :return: The padded attribute name
    """
    return "_{}".format(attr) if attr in backcompat_attrs else attr


def _prepare_multipart_body_helper(http_request: "HTTPRequestType", content_index: int = 0) -> int:
    """Helper for prepare_multipart_body.

    Will prepare the body of this request according to the multipart information.

    This call assumes the on_request policies have been applied already in their
    correct context (sync/async)

    Does nothing if "set_multipart_mixed" was never called.
    :param http_request: The http request whose multipart body we are trying
     to prepare
    :type http_request: any
    :param int content_index: The current index of parts within the batch message.
    :returns: The updated index after all parts in this request have been added.
    :rtype: int
    """
    if not http_request.multipart_mixed_info:
        return 0

    requests: Sequence["HTTPRequestType"] = http_request.multipart_mixed_info[0]
    boundary: Optional[str] = http_request.multipart_mixed_info[2]

    # Update the main request with the body
    main_message = Message()
    main_message.add_header("Content-Type", "multipart/mixed")
    if boundary:
        main_message.set_boundary(boundary)

    for req in requests:
        part_message = Message()
        if req.multipart_mixed_info:
            content_index = req.prepare_multipart_body(content_index=content_index)
            part_message.add_header("Content-Type", req.headers["Content-Type"])
            payload = req.serialize()
            # We need to remove the ~HTTP/1.1 prefix along with the added content-length
            payload = payload[payload.index(b"--") :]
        else:
            part_message.add_header("Content-Type", "application/http")
            part_message.add_header("Content-Transfer-Encoding", "binary")
            part_message.add_header("Content-ID", str(content_index))
            payload = req.serialize()
            content_index += 1
        part_message.set_payload(payload)
        main_message.attach(part_message)

    full_message = main_message.as_bytes(policy=HTTP)
    # From "as_bytes" doc:
    #  Flattening the message may trigger changes to the EmailMessage if defaults need to be filled in to complete
    #  the transformation to a string (for example, MIME boundaries may be generated or modified).
    # After this call, we know `get_boundary` will return a valid boundary and not None. Mypy doesn't know that.
    final_boundary: str = cast(str, main_message.get_boundary())
    eol = b"\r\n"
    _, _, body = full_message.split(eol, 2)
    http_request.set_bytes_body(body)
    http_request.headers["Content-Type"] = "multipart/mixed; boundary=" + final_boundary
    return content_index


class _HTTPSerializer(HTTPConnection):
    """Hacking the stdlib HTTPConnection to serialize HTTP request as strings."""

    def __init__(self, *args, **kwargs):
        self.buffer = b""
        kwargs.setdefault("host", "fakehost")
        super(_HTTPSerializer, self).__init__(*args, **kwargs)

    def putheader(self, header, *values):
        if header in ["Host", "Accept-Encoding"]:
            return
        super(_HTTPSerializer, self).putheader(header, *values)

    def send(self, data):
        self.buffer += data


def _serialize_request(http_request: "HTTPRequestType") -> bytes:
    """Helper for serialize.

    Serialize a request using the application/http spec/

    :param http_request: The http request which we are trying
     to serialize.
    :type http_request: any
    :rtype: bytes
    :return: The serialized request
    """
    if isinstance(http_request.body, dict):
        raise TypeError("Cannot serialize an HTTPRequest with dict body.")
    serializer = _HTTPSerializer()
    serializer.request(
        method=http_request.method,
        url=http_request.url,
        body=http_request.body,
        headers=http_request.headers,
    )
    return serializer.buffer
