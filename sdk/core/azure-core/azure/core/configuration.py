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
from typing import Union, Optional, Any, TypeVar, TYPE_CHECKING

from generic.core.configuration import Configuration as GenericConfiguration, ConnectionConfiguration

HTTPResponseType = TypeVar("HTTPResponseType")
HTTPRequestType = TypeVar("HTTPRequestType")

if TYPE_CHECKING:
    from .pipeline.policies import HTTPPolicy, AsyncHTTPPolicy, SansIOHTTPPolicy

    AnyPolicy = Union[
        HTTPPolicy[HTTPRequestType, HTTPResponseType],
        AsyncHTTPPolicy[HTTPRequestType, HTTPResponseType],
        SansIOHTTPPolicy[HTTPRequestType, HTTPResponseType],
    ]

class Configuration(GenericConfiguration):  # pylint: disable=too-many-instance-attributes
    """Provides the home for all of the configurable policies in the pipeline.

    A new Configuration object provides no default policies and does not specify in what
    order the policies will be added to the pipeline. The SDK developer must specify each
    of the policy defaults as required by the service and use the policies in the
    Configuration to construct the pipeline correctly, as well as inserting any
    unexposed/non-configurable policies.

    :ivar headers_policy: Provides parameters for custom or additional headers to be sent with the request.
    :ivar proxy_policy: Provides configuration parameters for proxy.
    :ivar redirect_policy: Provides configuration parameters for redirects.
    :ivar retry_policy: Provides configuration parameters for retries in the pipeline.
    :ivar custom_hook_policy: Provides configuration parameters for a custom hook.
    :ivar logging_policy: Provides configuration parameters for logging.
    :ivar http_logging_policy: Provides configuration parameters for HTTP specific logging.
    :ivar user_agent_policy: Provides configuration parameters to append custom values to the
     User-Agent header.
    :ivar authentication_policy: Provides configuration parameters for adding a bearer token Authorization
     header to requests.
    :ivar request_id_policy: Provides configuration parameters for adding a request id to requests.
    :keyword polling_interval: Polling interval while doing LRO operations, if Retry-After is not set.

    .. admonition:: Example:

        .. literalinclude:: ../samples/test_example_config.py
            :start-after: [START configuration]
            :end-before: [END configuration]
            :language: python
            :caption: Creates the service configuration and adds policies.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # Http logger configuration
        self.http_logging_policy: Optional[AnyPolicy[HTTPRequestType, HTTPResponseType]] = None

         # Custom hook configuration
        self.custom_hook_policy: Optional[AnyPolicy[HTTPRequestType, HTTPResponseType]] = None


__all__ = ["Configuration", "ConnectionConfiguration"]
