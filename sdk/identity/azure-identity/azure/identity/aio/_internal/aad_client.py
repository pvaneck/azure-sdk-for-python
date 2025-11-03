# ------------------------------------
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# ------------------------------------
import asyncio  # pylint: disable=do-not-import-asyncio
import time
from typing import Iterable, Optional, Union, Dict, Any

from azure.core.credentials import AccessTokenInfo
from azure.core.pipeline import AsyncPipeline
from azure.core.pipeline.policies import AsyncHTTPPolicy, SansIOHTTPPolicy
from azure.core.pipeline.transport import HttpRequest
from ..._internal import AadClientCertificate
from ..._internal import AadClientBase
from ..._internal.pipeline import build_async_pipeline
from ..._internal.utils import create_request_key

Policy = Union[AsyncHTTPPolicy, SansIOHTTPPolicy]


# pylint:disable=invalid-overridden-method
class AadClient(AadClientBase):  # pylint:disable=client-accepts-api-version-keyword

    def __init__(  # pylint:disable=missing-client-constructor-parameter-credential
        self, *args: Any, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        # Dictionary to store ongoing requests to prevent thundering herd
        self._pending_requests: Dict[str, asyncio.Task] = {}
        # Lock to protect the pending requests dictionary
        self._request_lock = asyncio.Lock()

    async def __aenter__(self) -> "AadClient":
        await self._pipeline.__aenter__()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self) -> None:
        """Close the client's transport session."""
        # Cancel any pending requests
        async with self._request_lock:
            for task in self._pending_requests.values():
                if not task.done():
                    task.cancel()
            self._pending_requests.clear()

        await self._pipeline.__aexit__()

    async def obtain_token_by_authorization_code(
        self, scopes: Iterable[str], code: str, redirect_uri: str, client_secret: Optional[str] = None, **kwargs
    ) -> AccessTokenInfo:
        request = self._get_auth_code_request(
            scopes=scopes, code=code, redirect_uri=redirect_uri, client_secret=client_secret, **kwargs
        )
        return await self._run_pipeline(request, **kwargs)

    async def obtain_token_by_client_certificate(
        self, scopes: Iterable[str], certificate: AadClientCertificate, **kwargs
    ) -> AccessTokenInfo:
        request = self._get_client_certificate_request(scopes, certificate, **kwargs)
        return await self._run_pipeline(request, stream=False, **kwargs)

    async def obtain_token_by_client_secret(self, scopes: Iterable[str], secret: str, **kwargs) -> AccessTokenInfo:
        request = self._get_client_secret_request(scopes, secret, **kwargs)
        return await self._run_pipeline(request, **kwargs)

    async def obtain_token_by_jwt_assertion(self, scopes: Iterable[str], assertion: str, **kwargs) -> AccessTokenInfo:
        request = self._get_jwt_assertion_request(scopes, assertion, **kwargs)
        return await self._run_pipeline(request, stream=False, **kwargs)

    async def obtain_token_by_refresh_token(
        self, scopes: Iterable[str], refresh_token: str, **kwargs
    ) -> AccessTokenInfo:
        request = self._get_refresh_token_request(scopes, refresh_token, **kwargs)
        return await self._run_pipeline(request, **kwargs)

    async def obtain_token_by_refresh_token_on_behalf_of(  # pylint: disable=name-too-long
        self,
        scopes: Iterable[str],
        client_credential: Union[str, AadClientCertificate, Dict[str, Any]],
        refresh_token: str,
        **kwargs
    ) -> AccessTokenInfo:
        request = self._get_refresh_token_on_behalf_of_request(
            scopes, client_credential=client_credential, refresh_token=refresh_token, **kwargs
        )
        return await self._run_pipeline(request, **kwargs)

    async def obtain_token_on_behalf_of(
        self,
        scopes: Iterable[str],
        client_credential: Union[str, AadClientCertificate, Dict[str, Any]],
        user_assertion: str,
        **kwargs
    ) -> AccessTokenInfo:
        request = self._get_on_behalf_of_request(
            scopes=scopes, client_credential=client_credential, user_assertion=user_assertion, **kwargs
        )
        return await self._run_pipeline(request, **kwargs)

    def _build_pipeline(self, **kwargs) -> AsyncPipeline:
        return build_async_pipeline(**kwargs)

    async def _execute_pipeline_request(self, request: HttpRequest, request_key: str, **kwargs) -> AccessTokenInfo:
        """Execute the actual pipeline request - this is what gets deduplicated.

        :param request: The HTTP request to execute.
        :type request: HttpRequest
        :param request_key: The key representing the request for deduplication.
        :type request_key: str
        :return: The access token information.
        :rtype: AccessTokenInfo
        """
        # remove tenant_id and claims kwarg that could have been passed from credential's get_token method
        # tenant_id is already part of `request` at this point
        try:
            kwargs.pop("tenant_id", None)
            kwargs.pop("claims", None)
            kwargs.pop("client_secret", None)
            enable_cae = kwargs.pop("enable_cae", False)
            now = int(time.time())
            response = await self._pipeline.run(request, retry_on_methods=self._POST, **kwargs)
            return self._process_response(response, now, enable_cae=enable_cae, **kwargs)
        finally:
            async with self._request_lock:
                if request_key in self._pending_requests:
                    del self._pending_requests[request_key]

    async def _run_pipeline(self, request: HttpRequest, **kwargs) -> AccessTokenInfo:
        """Run the pipeline with request coalescing to prevent sending duplicate requests.

        :param request: The HTTP request to run.
        :type request: HttpRequest
        :return: The access token information.
        :rtype: AccessTokenInfo
        """
        request_key = create_request_key(request)

        # Check if there's already a pending request for the same key
        async with self._request_lock:
            pending_task = self._pending_requests.get(request_key)
            if pending_task and not pending_task.done():
                task_to_wait = pending_task
            else:
                task_to_wait = asyncio.create_task(self._execute_pipeline_request(request, request_key, **kwargs))
                self._pending_requests[request_key] = task_to_wait

        return await task_to_wait
