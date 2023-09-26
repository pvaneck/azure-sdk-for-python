# --------------------------------------------------------------------------
#
# Copyright (c) Microsoft Corporation. All rights reserved.
#
# The MIT License (MIT)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the ""Software""), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED *AS IS*, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
# --------------------------------------------------------------------------
import pytest
import json
import requests
from unittest.mock import Mock

# module under test
from generic.core.exceptions import (
    HttpResponseError,
    SerializationError,
    DeserializationError,
)
from generic.core.rest._http_response_impl import _HttpResponseBaseImpl as RestHttpResponseBase
# from gener.core.rest import HttpRequest
from utils import HTTP_REQUESTS


class RestMockResponse(RestHttpResponseBase):
    def __init__(self, json_body):
        super(RestMockResponse, self).__init__(
            request=None,
            internal_response=None,
            status_code=400,
            reason="Bad Request",
            content_type="application/json",
            headers={},
            stream_download_generator=None,
        )

    def body(self):
        return self._body

    @property
    def content(self):
        return self._body


class FakeErrorOne(object):
    def __init__(self):
        self.error = Mock(message="A fake error", code="FakeErrorOne")


class FakeErrorTwo(object):
    def __init__(self):
        self.code = "FakeErrorTwo"
        self.message = "A different fake error"


class FakeHttpResponse(HttpResponseError):
    def __init__(self, response, error, *args, **kwargs):
        self.error = error
        super(FakeHttpResponse, self).__init__(self, response=response, *args, **kwargs)


class TestExceptions(object):
    def test_empty_httpresponse_error(self):
        error = HttpResponseError()
        assert str(error) == "Operation returned an invalid status 'None'"
        assert error.message == "Operation returned an invalid status 'None'"
        assert error.response is None
        assert error.reason is None
        assert error.status_code is None

    def test_message_httpresponse_error(self):
        error = HttpResponseError(message="Specific error message")
        assert str(error) == "Specific error message"
        assert error.message == "Specific error message"
        assert error.response is None
        assert error.reason is None
        assert error.status_code is None

    def test_error_continuation_token(self):
        error = HttpResponseError(message="Specific error message", continuation_token="foo")
        assert str(error) == "Specific error message"
        assert error.message == "Specific error message"
        assert error.response is None
        assert error.reason is None
        assert error.status_code is None
        assert error.continuation_token == "foo"

    @pytest.mark.parametrize("http_request", HTTP_REQUESTS)
    def test_httpresponse_error_with_response(self, client, port, http_request):
        request = http_request("GET", url="http://localhost:{}/basic/string".format(port))
        response = client.send_request(request, stream=False)
        error = HttpResponseError(response=response)
        assert error.message == "Operation returned an invalid status 'OK'"
        assert error.response is not None
        assert error.reason == "OK"
        assert isinstance(error.status_code, int)

    @pytest.mark.parametrize("http_request", HTTP_REQUESTS)
    def test_malformed_json(self, client, http_request):
        request = http_request("GET", "/errors/malformed-json")
        response = client.send_request(request)
        with pytest.raises(HttpResponseError) as ex:
            response.raise_for_status()
        assert (
            str(ex.value)
            == 'Operation returned an invalid status \'BAD REQUEST\'\nContent: {"code": 400, "error": {"global": ["MY-ERROR-MESSAGE-THAT-IS-COMING-FROM-THE-API"]'
        )

    @pytest.mark.parametrize("http_request", HTTP_REQUESTS)
    def test_text(self, client, http_request):
        request = http_request("GET", "/errors/text")
        response = client.send_request(request)
        with pytest.raises(HttpResponseError) as ex:
            response.raise_for_status()
        assert str(ex.value) == "Operation returned an invalid status 'BAD REQUEST'\nContent: I am throwing an error"


def test_serialization_error():
    message = "Oopsy bad input passed for serialization"
    error = SerializationError(message)
    with pytest.raises(SerializationError) as ex:
        raise error
    assert str(ex.value) == message

    with pytest.raises(ValueError) as ex:
        raise error
    assert str(ex.value) == message


def test_deserialization_error():
    message = "Oopsy bad input passed for serialization"
    error = DeserializationError(message)
    with pytest.raises(DeserializationError) as ex:
        raise error
    assert str(ex.value) == message

    with pytest.raises(ValueError) as ex:
        raise error
    assert str(ex.value) == message
