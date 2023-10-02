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
from typing import Union, Optional, Any, Generic, TypeVar, TYPE_CHECKING

HTTPResponseType = TypeVar("HTTPResponseType")
HTTPRequestType = TypeVar("HTTPRequestType")

if TYPE_CHECKING:
    from .pipeline.policies import HTTPPolicy, AsyncHTTPPolicy, SansIOHTTPPolicy

    AnyPolicy = Union[
        HTTPPolicy[HTTPRequestType, HTTPResponseType],
        AsyncHTTPPolicy[HTTPRequestType, HTTPResponseType],
        SansIOHTTPPolicy[HTTPRequestType, HTTPResponseType],
    ]


class Configuration(Generic[HTTPRequestType, HTTPResponseType]):  # pylint: disable=too-many-instance-attributes
    """Provides the home for all of the configurable policies in the pipeline.

    A new Configuration object provides no default policies and does not specify in what
    order the policies will be added to the pipeline. The SDK developer must specify each
    of the policy defaults as required by the service and use the policies in the
    Configuration to construct the pipeline correctly, as well as inserting any
    unexposed/non-configurable policies.

    :ivar headers_policy: Provides parameters for custom or additional headers to be sent with the request.
    :ivar proxy_policy: Provides configuration parameters for proxy.
    :ivar retry_policy: Provides configuration parameters for retries in the pipeline.
    :ivar logging_policy: Provides configuration parameters for logging.
    :ivar user_agent_policy: Provides configuration parameters to append custom values to the
     User-Agent header.
    :ivar authentication_policy: Provides configuration parameters for adding a bearer token Authorization
     header to requests.

    .. admonition:: Example:

        .. literalinclude:: ../samples/test_example_config.py
            :start-after: [START configuration]
            :end-before: [END configuration]
            :language: python
            :caption: Creates the service configuration and adds policies.
    """

    def __init__(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        # Headers (sent with every request)
        self.headers_policy: Optional[AnyPolicy[HTTPRequestType, HTTPResponseType]] = None

        # Proxy settings (Currently used to configure transport, could be pipeline policy instead)
        self.proxy_policy: Optional[AnyPolicy[HTTPRequestType, HTTPResponseType]] = None

        # Retry configuration
        self.retry_policy: Optional[AnyPolicy[HTTPRequestType, HTTPResponseType]] = None

        # Logger configuration
        self.logging_policy: Optional[AnyPolicy[HTTPRequestType, HTTPResponseType]] = None

        # User Agent configuration
        self.user_agent_policy: Optional[AnyPolicy[HTTPRequestType, HTTPResponseType]] = None

        # Authentication configuration
        self.authentication_policy: Optional[AnyPolicy[HTTPRequestType, HTTPResponseType]] = None
