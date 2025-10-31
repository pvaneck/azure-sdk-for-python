# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See LICENSE.txt in the project root for
# license information.
# -------------------------------------------------------------------------
import asyncio
import base64
import pickle
import sys
import time
from unittest.mock import Mock, patch, AsyncMock, create_autospec
from requests import Response


from azure.core.credentials import AccessToken, AccessTokenInfo
from azure.core.credentials_async import AsyncTokenCredential, AsyncSupportsTokenInfo
from azure.core.exceptions import ServiceRequestError, HttpResponseError, ClientAuthenticationError
from azure.core.pipeline import AsyncPipeline, PipelineRequest, PipelineContext, PipelineResponse
from azure.core.pipeline.policies import (
    AsyncBearerTokenCredentialPolicy,
    SansIOHTTPPolicy,
    AsyncRedirectPolicy,
    SensitiveHeaderCleanupPolicy,
)
from azure.core.pipeline.policies._authentication import BACKGROUND_REFRESH_WINDOW_SECONDS
from azure.core.pipeline.transport import AsyncHttpTransport, HttpRequest
import pytest
import trio

from utils import HTTP_REQUESTS


# Picklable credential class for testing
class PicklableCredential(AsyncTokenCredential):
    def __init__(self):
        self.token = AccessToken("test_token", int(time.time()) + 3600)

    async def get_token(self, *scopes, **kwargs):
        return self.token

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type=None, exc_val=None, exc_tb=None):
        pass


# Slow refresh credential for testing background task scenarios
class SlowRefreshCredential(AsyncTokenCredential):
    def __init__(self):
        self.call_count = 0
        # Create a token that will trigger background refresh
        self.token = AccessToken("initial_token", int(time.time()) + 3600)

    async def get_token(self, *scopes, **kwargs):
        self.call_count += 1
        # First call returns initial token, subsequent calls simulate slow refresh
        if self.call_count == 1:
            return self.token
        else:
            # Simulate a slow token refresh by sleeping
            await asyncio.sleep(0.1)
            return AccessToken("refreshed_token", int(time.time()) + 3600)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type=None, exc_val=None, exc_tb=None):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_bearer_policy_adds_header(http_request):
    """The bearer token policy should add a header containing a token from its credential"""
    # 2524608000 == 01/01/2050 @ 12:00am (UTC)
    expected_token = AccessToken("expected_token", 2524608000)

    async def verify_authorization_header(request):
        assert request.http_request.headers["Authorization"] == "Bearer {}".format(expected_token.token)
        return Mock()

    get_token_calls = 0

    async def get_token(*_, **__):
        nonlocal get_token_calls
        get_token_calls += 1
        return expected_token

    fake_credential = Mock(spec_set=["get_token"], get_token=get_token)
    policies = [AsyncBearerTokenCredentialPolicy(fake_credential, "scope"), Mock(send=verify_authorization_header)]
    pipeline = AsyncPipeline(transport=Mock(), policies=policies)

    await pipeline.run(http_request("GET", "https://spam.eggs"), context=None)
    assert get_token_calls == 1

    await pipeline.run(http_request("GET", "https://spam.eggs"), context=None)
    # Didn't need a new token
    assert get_token_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_bearer_policy_authorize_request(http_request):
    """The authorize_request method should add a header containing a token from its credential"""
    # 2524608000 == 01/01/2050 @ 12:00am (UTC)
    expected_token = AccessToken("expected_token", 2524608000)

    fake_credential = Mock(spec_set=["get_token"], get_token=Mock(return_value=expected_token))
    policy = AsyncBearerTokenCredentialPolicy(fake_credential, "scope")
    http_req = http_request("GET", "https://spam.eggs")
    request = PipelineRequest(http_req, PipelineContext(None))

    await policy.authorize_request(request, "scope", claims="foo")
    assert policy._token is expected_token
    assert http_req.headers["Authorization"] == f"Bearer {expected_token.token}"
    assert fake_credential.get_token.call_count == 1
    assert fake_credential.get_token.call_args[0] == ("scope",)
    assert fake_credential.get_token.call_args[1] == {"claims": "foo"}


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_bearer_policy_adds_header_access_token_info(http_request):
    """The bearer token policy should also add an auth header when an AccessTokenInfo is returned."""
    # 2524608000 == 01/01/2050 @ 12:00am (UTC)
    access_token = AccessToken("other_token", 2524608000)
    expected_token = AccessTokenInfo("expected_token", 2524608000, refresh_on=2524608000)

    async def verify_authorization_header(request):
        assert request.http_request.headers["Authorization"] == "Bearer {}".format(expected_token.token)
        return Mock()

    get_token_calls = 0
    get_token_info_calls = 0

    class MockCredential(AsyncTokenCredential):
        async def get_token(self, *_, **__):
            nonlocal get_token_calls
            get_token_calls += 1
            return access_token

        async def get_token_info(*_, **__):
            nonlocal get_token_info_calls
            get_token_info_calls += 1
            return expected_token

    fake_credential: AsyncTokenCredential = MockCredential()
    policies = [AsyncBearerTokenCredentialPolicy(fake_credential, "scope"), Mock(send=verify_authorization_header)]
    pipeline = AsyncPipeline(transport=AsyncMock(), policies=policies)

    await pipeline.run(http_request("GET", "https://spam.eggs"), context=None)
    assert get_token_info_calls == 1

    await pipeline.run(http_request("GET", "https://spam.eggs"), context=None)
    # Didn't need a new token
    assert get_token_info_calls == 1
    # get_token should not have been called
    assert get_token_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_bearer_policy_authorize_request_access_token_info(http_request):
    """The authorize_request method should add a header containing a token from its credential"""
    # 2524608000 == 01/01/2050 @ 12:00am (UTC)
    expected_token = AccessTokenInfo("expected_token", 2524608000)
    fake_credential = Mock(get_token=Mock(), get_token_info=Mock(return_value=expected_token))
    policy = AsyncBearerTokenCredentialPolicy(fake_credential, "scope")
    http_req = http_request("GET", "https://spam.eggs")
    request = PipelineRequest(http_req, PipelineContext(None))

    await policy.authorize_request(request, "scope", claims="foo")
    assert policy._token is expected_token
    assert http_req.headers["Authorization"] == f"Bearer {expected_token.token}"
    assert fake_credential.get_token_info.call_args[0] == ("scope",)
    assert fake_credential.get_token_info.call_args[1] == {"options": {"claims": "foo"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_bearer_policy_send(http_request):
    """The bearer token policy should invoke the next policy's send method and return the result"""
    expected_request = http_request("GET", "https://spam.eggs")
    expected_response = Mock()

    async def verify_request(request):
        assert request.http_request is expected_request
        return expected_response

    get_token = get_completed_future(AccessToken("***", 42))
    fake_credential = Mock(spec_set=["get_token"], get_token=lambda *_, **__: get_token)
    policies = [AsyncBearerTokenCredentialPolicy(fake_credential, "scope"), Mock(send=verify_request)]
    response = await AsyncPipeline(transport=Mock(), policies=policies).run(expected_request)

    assert response is expected_response


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_bearer_policy_sync_send(http_request):
    """The bearer token policy should invoke the next policy's send method and return the result"""
    expected_request = http_request("GET", "https://spam.eggs")
    expected_response = Mock()

    async def verify_request(request):
        assert request.http_request is expected_request
        return expected_response

    get_token = get_completed_future(AccessToken("***", 42))
    fake_credential = Mock(spec_set=["get_token"], get_token=lambda *_, **__: get_token)
    policies = [AsyncBearerTokenCredentialPolicy(fake_credential, "scope"), Mock(send=verify_request)]
    response = await AsyncPipeline(transport=Mock(), policies=policies).run(expected_request)

    assert response is expected_response


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_bearer_policy_token_caching(http_request):
    good_for_one_hour = AccessToken("token", int(time.time() + 3600))
    expected_token = good_for_one_hour
    get_token_calls = 0

    async def get_token(*_, **__):
        nonlocal get_token_calls
        get_token_calls += 1
        return expected_token

    credential = Mock(spec_set=["get_token"], get_token=get_token)
    policies = [
        AsyncBearerTokenCredentialPolicy(credential, "scope"),
        Mock(send=Mock(return_value=get_completed_future(Mock()))),
    ]
    pipeline = AsyncPipeline(transport=Mock, policies=policies)

    await pipeline.run(http_request("GET", "https://spam.eggs"))
    assert get_token_calls == 1  # policy has no token at first request -> it should call get_token

    await pipeline.run(http_request("GET", "https://spam.eggs"))
    assert get_token_calls == 1  # token is good for an hour -> policy should return it from cache

    expired_token = AccessToken("token", int(time.time()))
    get_token_calls = 0
    expected_token = expired_token
    policies = [
        AsyncBearerTokenCredentialPolicy(credential, "scope"),
        Mock(send=lambda _: get_completed_future(Mock())),
    ]
    pipeline = AsyncPipeline(transport=Mock(), policies=policies)

    await pipeline.run(http_request("GET", "https://spam.eggs"))
    assert get_token_calls == 1

    await pipeline.run(http_request("GET", "https://spam.eggs"))
    assert get_token_calls == 2  # token expired -> policy should call get_token


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_bearer_policy_access_token_info_caching(http_request):
    """The policy should cache AccessTokenInfo instances and refresh them when necessary."""

    good_for_one_hour = AccessTokenInfo("token", int(time.time() + 3600))

    credential = create_autospec(AsyncSupportsTokenInfo, instance=True, spec_set=True)
    credential.get_token_info = AsyncMock(return_value=good_for_one_hour)
    pipeline = AsyncPipeline(transport=AsyncMock(), policies=[AsyncBearerTokenCredentialPolicy(credential, "scope")])

    await pipeline.run(http_request("GET", "https://spam.eggs"))
    assert (
        credential.get_token_info.call_count == 1
    )  # policy has no token at first request -> it should call get_token_info

    await pipeline.run(http_request("GET", "https://spam.eggs"))
    assert credential.get_token_info.call_count == 1  # token is good for an hour -> policy should return it from cache

    expired_token = AccessTokenInfo("token", int(time.time()))
    credential.get_token_info.reset_mock()
    credential.get_token_info.return_value = expired_token
    pipeline = AsyncPipeline(transport=AsyncMock(), policies=[AsyncBearerTokenCredentialPolicy(credential, "scope")])

    await pipeline.run(http_request("GET", "https://spam.eggs"))
    assert credential.get_token_info.call_count == 1

    await pipeline.run(http_request("GET", "https://spam.eggs"))
    assert credential.get_token_info.call_count == 2  # token is expired -> policy should call get_token_info again

    refreshable_token = AccessTokenInfo("token", int(time.time() + 100), refresh_on=int(time.time() - 1))
    credential.get_token_info.reset_mock()
    credential.get_token_info.return_value = refreshable_token
    pipeline = AsyncPipeline(transport=AsyncMock(), policies=[AsyncBearerTokenCredentialPolicy(credential, "scope")])

    await pipeline.run(http_request("GET", "https://spam.eggs"))
    assert credential.get_token_info.call_count == 1

    await pipeline.run(http_request("GET", "https://spam.eggs"))
    assert credential.get_token_info.call_count == 2  # token refresh-on time has passed, call again


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_bearer_policy_optionally_enforces_https(http_request):
    """HTTPS enforcement should be controlled by a keyword argument, and enabled by default"""

    async def assert_option_popped(request, **kwargs):
        assert "enforce_https" not in kwargs, "AsyncBearerTokenCredentialPolicy didn't pop the 'enforce_https' option"
        return Mock()

    credential = Mock(spec_set=["get_token"], get_token=lambda *_, **__: get_completed_future(AccessToken("***", 42)))
    pipeline = AsyncPipeline(
        transport=Mock(send=assert_option_popped), policies=[AsyncBearerTokenCredentialPolicy(credential, "scope")]
    )

    # by default and when enforce_https=True, the policy should raise when given an insecure request
    with pytest.raises(ServiceRequestError):
        await pipeline.run(http_request("GET", "http://not.secure"))
    with pytest.raises(ServiceRequestError):
        await pipeline.run(http_request("GET", "http://not.secure"), enforce_https=True)

    # when enforce_https=False, an insecure request should pass
    await pipeline.run(http_request("GET", "http://not.secure"), enforce_https=False)

    # https requests should always pass
    await pipeline.run(http_request("GET", "https://secure"), enforce_https=False)
    await pipeline.run(http_request("GET", "https://secure"), enforce_https=True)
    await pipeline.run(http_request("GET", "https://secure"))


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_bearer_policy_preserves_enforce_https_opt_out(http_request):
    """The policy should use request context to preserve an opt out from https enforcement"""

    class ContextValidator(SansIOHTTPPolicy):
        def on_request(self, request):
            assert "enforce_https" in request.context, "'enforce_https' is not in the request's context"
            return Mock()

    get_token = get_completed_future(AccessToken("***", 42))
    credential = Mock(spec_set=["get_token"], get_token=lambda *_, **__: get_token)
    policies = [AsyncBearerTokenCredentialPolicy(credential, "scope"), ContextValidator()]
    pipeline = AsyncPipeline(transport=Mock(send=lambda *_, **__: get_completed_future(Mock())), policies=policies)

    await pipeline.run(http_request("GET", "http://not.secure"), enforce_https=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_bearer_policy_context_unmodified_by_default(http_request):
    """When no options for the policy accompany a request, the policy shouldn't add anything to the request context"""

    class ContextValidator(SansIOHTTPPolicy):
        def on_request(self, request):
            assert not any(request.context), "the policy shouldn't add to the request's context"
            return Mock()

    get_token = get_completed_future(AccessToken("***", 42))
    credential = Mock(spec_set=["get_token"], get_token=lambda *_, **__: get_token)
    policies = [AsyncBearerTokenCredentialPolicy(credential, "scope"), ContextValidator()]
    pipeline = AsyncPipeline(transport=Mock(send=lambda *_, **__: get_completed_future(Mock())), policies=policies)

    await pipeline.run(http_request("GET", "https://secure"))


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_bearer_policy_calls_sansio_methods(http_request):
    """AsyncBearerTokenCredentialPolicy should call SansIOHttpPolicy methods as does _SansIOAsyncHTTPPolicyRunner"""

    class TestPolicy(AsyncBearerTokenCredentialPolicy):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.on_exception = Mock(return_value=False)
            self.on_request = Mock()
            self.on_response = Mock()

        async def send(self, request):
            self.request = request
            self.response = await super().send(request)
            return self.response

    credential = Mock(
        spec_set=["get_token"],
        get_token=Mock(return_value=get_completed_future(AccessToken("***", int(time.time()) + 3600))),
    )
    policy = TestPolicy(credential, "scope")
    transport = Mock(send=Mock(return_value=get_completed_future(Mock(status_code=200))))

    pipeline = AsyncPipeline(transport=transport, policies=[policy])
    await pipeline.run(http_request("GET", "https://localhost"))

    policy.on_request.assert_called_once_with(policy.request)
    policy.on_response.assert_called_once_with(policy.request, policy.response)

    # the policy should call on_exception when next.send() raises
    class TestException(Exception):
        pass

    # during the first send...
    transport = Mock(send=Mock(side_effect=TestException))
    policy = TestPolicy(credential, "scope")
    pipeline = AsyncPipeline(transport=transport, policies=[policy])
    with pytest.raises(TestException):
        await pipeline.run(http_request("GET", "https://localhost"))
    policy.on_exception.assert_called_once_with(policy.request)

    # ...or the second
    async def fake_send(*args, **kwargs):
        if fake_send.calls == 0:
            fake_send.calls = 1
            return Mock(status_code=401, headers={"WWW-Authenticate": 'Basic realm="localhost"'})
        raise TestException()

    fake_send.calls = 0

    policy = TestPolicy(credential, "scope")
    policy.on_challenge = Mock(return_value=get_completed_future(True))
    transport = Mock(send=Mock(wraps=fake_send))
    pipeline = AsyncPipeline(transport=transport, policies=[policy])
    with pytest.raises(TestException):
        await pipeline.run(http_request("GET", "https://localhost"))
    assert transport.send.call_count == 2
    policy.on_challenge.assert_called_once()
    policy.on_exception.assert_called_once_with(policy.request)


def get_completed_future(result=None):
    fut = asyncio.Future()
    fut.set_result(result)
    return fut


@pytest.mark.asyncio
async def test_bearer_policy_redirect_same_domain():
    class MockTransport(AsyncHttpTransport):
        def __init__(self):
            self._first = True

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def close(self):
            pass

        async def open(self):
            pass

        async def send(self, request, **kwargs):  # type: (PipelineRequest, Any) -> PipelineResponse
            if self._first:
                self._first = False
                assert request.headers["Authorization"] == "Bearer {}".format(auth_headder)
                response = Response()
                response.status_code = 301
                response.headers["location"] = "https://localhost"
                return response
            assert request.headers["Authorization"] == "Bearer {}".format(auth_headder)
            response = Response()
            response.status_code = 200
            return response

    auth_headder = "token"
    expected_scope = "scope"

    async def get_token(*_, **__):
        token = AccessToken(auth_headder, 0)
        return token

    credential = Mock(spec_set=["get_token"], get_token=get_token)
    auth_policy = AsyncBearerTokenCredentialPolicy(credential, expected_scope)
    redirect_policy = AsyncRedirectPolicy()
    header_clean_up_policy = SensitiveHeaderCleanupPolicy()
    pipeline = AsyncPipeline(transport=MockTransport(), policies=[redirect_policy, auth_policy, header_clean_up_policy])

    await pipeline.run(HttpRequest("GET", "https://localhost"))


@pytest.mark.asyncio
async def test_bearer_policy_redirect_different_domain():
    class MockTransport(AsyncHttpTransport):
        def __init__(self):
            self._first = True

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def close(self):
            pass

        async def open(self):
            pass

        async def send(self, request, **kwargs):  # type: (PipelineRequest, Any) -> PipelineResponse
            if self._first:
                self._first = False
                assert request.headers["Authorization"] == "Bearer {}".format(auth_headder)
                response = Response()
                response.status_code = 301
                response.headers["location"] = "https://localhost1"
                return response
            assert not request.headers.get("Authorization")
            response = Response()
            response.status_code = 200
            return response

    auth_headder = "token"
    expected_scope = "scope"

    async def get_token(*_, **__):
        token = AccessToken(auth_headder, 0)
        return token

    credential = Mock(spec_set=["get_token"], get_token=get_token)
    auth_policy = AsyncBearerTokenCredentialPolicy(credential, expected_scope)
    redirect_policy = AsyncRedirectPolicy()
    header_clean_up_policy = SensitiveHeaderCleanupPolicy()
    pipeline = AsyncPipeline(transport=MockTransport(), policies=[redirect_policy, auth_policy, header_clean_up_policy])

    await pipeline.run(HttpRequest("GET", "https://localhost"))


@pytest.mark.asyncio
async def test_bearer_policy_redirect_opt_out_clean_up():
    class MockTransport(AsyncHttpTransport):
        def __init__(self):
            self._first = True

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def close(self):
            pass

        async def open(self):
            pass

        async def send(self, request, **kwargs):  # type: (PipelineRequest, Any) -> PipelineResponse
            if self._first:
                self._first = False
                assert request.headers["Authorization"] == "Bearer {}".format(auth_headder)
                response = Response()
                response.status_code = 301
                response.headers["location"] = "https://localhost1"
                return response
            assert request.headers["Authorization"] == "Bearer {}".format(auth_headder)
            response = Response()
            response.status_code = 200
            return response

    auth_headder = "token"
    expected_scope = "scope"

    async def get_token(*_, **__):
        token = AccessToken(auth_headder, 0)
        return token

    credential = Mock(spec_set=["get_token"], get_token=get_token)
    auth_policy = AsyncBearerTokenCredentialPolicy(credential, expected_scope)
    redirect_policy = AsyncRedirectPolicy()
    header_clean_up_policy = SensitiveHeaderCleanupPolicy(disable_redirect_cleanup=True)
    pipeline = AsyncPipeline(transport=MockTransport(), policies=[redirect_policy, auth_policy, header_clean_up_policy])

    await pipeline.run(HttpRequest("GET", "https://localhost"))


@pytest.mark.asyncio
async def test_bearer_policy_redirect_customize_sensitive_headers():
    class MockTransport(AsyncHttpTransport):
        def __init__(self):
            self._first = True

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def close(self):
            pass

        async def open(self):
            pass

        async def send(self, request, **kwargs):  # type: (PipelineRequest, Any) -> PipelineResponse
            if self._first:
                self._first = False
                assert request.headers["Authorization"] == "Bearer {}".format(auth_headder)
                response = Response()
                response.status_code = 301
                response.headers["location"] = "https://localhost1"
                return response
            assert request.headers.get("Authorization")
            response = Response()
            response.status_code = 200
            return response

    auth_headder = "token"
    expected_scope = "scope"

    async def get_token(*_, **__):
        token = AccessToken(auth_headder, 0)
        return token

    credential = Mock(spec_set=["get_token"], get_token=get_token)
    auth_policy = AsyncBearerTokenCredentialPolicy(credential, expected_scope)
    redirect_policy = AsyncRedirectPolicy()
    header_clean_up_policy = SensitiveHeaderCleanupPolicy(blocked_redirect_headers=["x-ms-authorization-auxiliary"])
    pipeline = AsyncPipeline(transport=MockTransport(), policies=[redirect_policy, auth_policy, header_clean_up_policy])

    await pipeline.run(HttpRequest("GET", "https://localhost"))


@pytest.mark.asyncio
async def test_async_token_credential_inheritance():
    class TestTokenCredential(AsyncTokenCredential):
        async def get_token(self, *scopes, **kwargs):
            return "TOKEN"

    cred = TestTokenCredential()
    await cred.get_token("scope")


@pytest.mark.asyncio
async def test_async_token_credential_asyncio_lock():
    auth_policy = AsyncBearerTokenCredentialPolicy(Mock(), "scope")
    assert isinstance(auth_policy._lock, asyncio.Lock)


@pytest.mark.trio
async def test_async_token_credential_trio_lock():
    auth_policy = AsyncBearerTokenCredentialPolicy(Mock(), "scope")
    assert isinstance(auth_policy._lock, trio.Lock)


def test_async_token_credential_sync():
    """Verify that AsyncBearerTokenCredentialPolicy can be constructed in a synchronous context."""
    auth_policy = AsyncBearerTokenCredentialPolicy(Mock(), "scope")
    with patch.dict("sys.modules"):
        # Ensure trio isn't in sys.modules (i.e. imported).
        sys.modules.pop("trio", None)
        AsyncBearerTokenCredentialPolicy(Mock(), "scope")


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_async_bearer_policy_on_challenge_caches_token(http_request):
    """Test that async on_challenge caches the token when handling claims challenges"""
    # Setup credentials that return different tokens for different calls
    initial_token = AccessToken("initial_token", int(time.time()) + 3600)
    claims_token = AccessToken("claims_token", int(time.time()) + 3600)

    call_count = 0

    async def mock_get_token_info(*scopes, options=None):
        nonlocal call_count
        call_count += 1
        if options and "claims" in options:
            return claims_token
        return initial_token

    fake_credential = Mock(spec_set=["get_token_info"], get_token_info=mock_get_token_info)
    policy = AsyncBearerTokenCredentialPolicy(fake_credential, "scope")

    # Create request and initial response
    http_req = http_request("GET", "https://example.com")
    request = PipelineRequest(http_req, PipelineContext(None))

    # Create a 401 response with insufficient_claims challenge
    test_claims = '{"access_token":{"foo":"bar"}}'
    encoded_claims = base64.urlsafe_b64encode(test_claims.encode()).decode().rstrip("=")
    challenge_header = f'Bearer error="insufficient_claims", claims="{encoded_claims}"'

    response_mock = Mock(status_code=401, headers={"WWW-Authenticate": challenge_header})
    response = PipelineResponse(request, response_mock, PipelineContext(None))

    # Call on_challenge
    result = await policy.on_challenge(request, response)

    # Verify the challenge was handled successfully
    assert result is True

    # Verify the token was cached
    assert policy._token is claims_token
    assert policy._token.token == "claims_token"

    # Verify the Authorization header was set correctly
    assert request.http_request.headers["Authorization"] == "Bearer claims_token"


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_async_bearer_policy_on_challenge_exception_chaining(http_request):
    """Test that exceptions during async on_challenge are chained with HttpResponseError"""

    # Mock credential that raises an exception during get_token_info with claims
    async def mock_get_token_info(*scopes, options=None):
        if options and "claims" in options:
            raise ClientAuthenticationError("Failed to request token info with claims")
        return AccessTokenInfo("initial_token", int(time.time()) + 3600)

    fake_credential = Mock(
        spec_set=["get_token", "get_token_info"],
        get_token=AsyncMock(return_value=AccessToken("fallback", int(time.time()) + 3600)),
        get_token_info=mock_get_token_info,
    )
    policy = AsyncBearerTokenCredentialPolicy(fake_credential, "scope")

    # Create a 401 response with insufficient_claims challenge
    test_claims = '{"access_token":{"foo":"bar"}}'
    encoded_claims = base64.urlsafe_b64encode(test_claims.encode()).decode().rstrip("=")
    challenge_header = f'Bearer error="insufficient_claims", claims="{encoded_claims}"'

    response_mock = Mock(status_code=401, headers={"WWW-Authenticate": challenge_header})

    # Mock transport that returns the 401 response
    async def mock_transport_send(request):
        return response_mock

    transport = Mock(send=mock_transport_send)
    pipeline = AsyncPipeline(transport=transport, policies=[policy])

    # Execute the request and verify exception chaining
    with pytest.raises(ClientAuthenticationError) as exc_info:
        await pipeline.run(http_request("GET", "https://example.com"))

    # Verify the original exception is preserved
    original_exception = exc_info.value
    assert original_exception.message == "Failed to request token info with claims"

    # Verify the exception is chained with HttpResponseError
    assert original_exception.__cause__ is not None
    assert isinstance(original_exception.__cause__, HttpResponseError)

    # Verify the HttpResponseError contains the original 401 response
    http_response_error = original_exception.__cause__
    assert http_response_error.response is response_mock


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_async_bearer_policy_reads_streamed_response_on_challenge_exception(http_request):
    """Test that the async policy reads streamed response body when on_challenge raises exception"""

    # Create a credential that will raise an exception when get_token is called with claims
    async def failing_get_token(*scopes, **kwargs):
        if "claims" in kwargs:
            raise ClientAuthenticationError("Failed to get token with claims")
        return AccessToken("initial_token", int(time.time()) + 3600)

    credential = Mock(spec_set=["get_token"], get_token=failing_get_token)
    policy = AsyncBearerTokenCredentialPolicy(credential, "scope")

    # Create a 401 response with insufficient_claims challenge that will trigger the exception
    test_claims = '{"access_token":{"foo":"bar"}}'
    encoded_claims = base64.urlsafe_b64encode(test_claims.encode()).decode().rstrip("=")
    challenge_header = f'Bearer error="insufficient_claims", claims="{encoded_claims}"'

    # Create the mock HTTP response with stream reading capability
    http_response_mock = Mock()
    http_response_mock.status_code = 401
    http_response_mock.headers = {"WWW-Authenticate": challenge_header}
    http_response_mock.read = AsyncMock(return_value=b"Error details from server")

    # Mock transport that returns the HTTP response directly (it will be wrapped by Pipeline)
    async def mock_transport_send(request, **kwargs):
        return http_response_mock

    transport = Mock(send=mock_transport_send)

    # Create pipeline with stream option
    pipeline = AsyncPipeline(transport=transport, policies=[policy])

    # Execute the request and verify exception handling
    with pytest.raises(ClientAuthenticationError) as exc_info:
        await pipeline.run(http_request("GET", "https://example.com"), stream=True)

    # Verify that the response.read() was called to consume the stream
    http_response_mock.read.assert_called_once()

    # Verify the exception chaining
    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, HttpResponseError)


@pytest.mark.asyncio
async def test_bearer_policy_is_picklable():
    """Test that AsyncBearerTokenCredentialPolicy can be pickled and unpickled"""

    # Create a credential instance
    credential = PicklableCredential()

    # Create the policy instance
    policy = AsyncBearerTokenCredentialPolicy(credential, "https://example.com/.default")

    # Test basic pickling
    serialized = pickle.dumps(policy)
    deserialized_policy = pickle.loads(serialized)  # nosec

    # Verify the policy was deserialized correctly
    assert isinstance(deserialized_policy, AsyncBearerTokenCredentialPolicy)
    assert deserialized_policy._scopes == policy._scopes
    assert deserialized_policy._enable_cae == policy._enable_cae

    # Test pickling with state
    # Set a token on the original policy to verify state is preserved
    test_token = AccessToken("preserved_token", int(time.time()) + 3600)
    policy._token = test_token

    # Pickle and unpickle again
    serialized_with_token = pickle.dumps(policy)
    deserialized_with_token = pickle.loads(serialized_with_token)  # nosec

    # Verify the token state was preserved
    assert deserialized_with_token._token is not None
    assert deserialized_with_token._token.token == "preserved_token"
    assert deserialized_with_token._token.expires_on == test_token.expires_on

    # Test that the deserialized policy can still function
    # (Note: We can't easily test the async functionality due to asyncio loop issues in pickle,
    # but we can verify the basic structure is intact)
    assert callable(deserialized_with_token._credential.get_token)
    assert hasattr(deserialized_with_token, "_lock_instance")
    assert hasattr(deserialized_with_token, "_background_refresh_task")


@pytest.mark.asyncio
async def test_bearer_policy_picklable_with_background_task():
    """Test that AsyncBearerTokenCredentialPolicy can be pickled even with an active background refresh task"""

    credential = SlowRefreshCredential()
    policy = AsyncBearerTokenCredentialPolicy(credential, "https://example.com/.default")

    expires_soon = int(time.time()) + BACKGROUND_REFRESH_WINDOW_SECONDS + 100
    policy._token = AccessToken("expiring_token", expires_soon)

    # Trigger background refresh by calling _try_background_refresh
    background_started = policy._try_background_refresh()
    assert background_started, "Background refresh should have started"
    assert policy._background_refresh_task is not None, "Background task should be created"
    assert not policy._background_refresh_task.done(), "Background task should still be running"

    # Store the original task for verification
    original_task = policy._background_refresh_task

    # Now test pickling with active background task
    # With __getstate__ customization, this should now succeed
    serialized = pickle.dumps(policy)

    # Give the cancellation a moment to process
    await asyncio.sleep(0.01)

    # Verify the original task was cancelled during pickling
    assert original_task.cancelled(), "Original background task should have been cancelled during pickling"

    # Test unpickling
    deserialized_policy = pickle.loads(serialized)  # nosec

    # Verify the policy structure is intact
    assert isinstance(deserialized_policy, AsyncBearerTokenCredentialPolicy)
    assert deserialized_policy._scopes == policy._scopes
    assert deserialized_policy._enable_cae == policy._enable_cae

    # The token should be preserved
    assert deserialized_policy._token is not None
    assert deserialized_policy._token.token == "expiring_token"

    # The background task should be None after unpickling
    assert deserialized_policy._background_refresh_task is None

    # The lock instance should also be None after unpickling
    assert deserialized_policy._lock_instance is None

    # Verify that the deserialized policy can create new background tasks when needed
    # Set a token that's close to expiry again
    deserialized_policy._token = AccessToken("another_expiring_token", expires_soon)

    # This should create a new background task
    new_background_started = deserialized_policy._try_background_refresh()
    assert new_background_started, "Deserialized policy should be able to start new background refresh"
    assert deserialized_policy._background_refresh_task is not None, "New background task should be created"

    # Clean up - cancel the new background task
    if deserialized_policy._background_refresh_task and not deserialized_policy._background_refresh_task.done():
        task_to_cancel = deserialized_policy._background_refresh_task
        task_to_cancel.cancel()
        try:
            await asyncio.shield(task_to_cancel)
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            pass


# Background refresh credential that tracks calls and allows control
class BackgroundRefreshTestCredential(AsyncTokenCredential):
    def __init__(self):
        self.get_token_call_count = 0
        self.tokens_to_return = []
        self.get_token_delay = 0.0
        self.should_fail = False
        self.fail_once = False
        self._lock = asyncio.Lock()
        self.last_exception = None

    def set_tokens(self, tokens):
        """Set the sequence of tokens to return on get_token calls."""
        self.tokens_to_return = tokens

    def set_delay(self, delay):
        """Set delay for get_token calls to simulate slow refresh."""
        self.get_token_delay = delay

    def set_failure(self, should_fail=True, fail_once=False):
        """Configure credential to fail on get_token calls."""
        self.should_fail = should_fail
        self.fail_once = fail_once

    async def get_token(self, *scopes, **kwargs):
        async with self._lock:
            self.get_token_call_count += 1

            try:
                if self.should_fail:
                    if self.fail_once:
                        self.should_fail = False
                    raise ClientAuthenticationError("Simulated credential failure")

                if self.get_token_delay > 0:
                    await asyncio.sleep(self.get_token_delay)

                if self.tokens_to_return:
                    # Return the latest (refreshed) token from the second element onward
                    if len(self.tokens_to_return) > 1:
                        token = self.tokens_to_return[1]  # Always return the refreshed token
                    else:
                        token = self.tokens_to_return[0]
                    return token

                # Default token
                default_token = AccessToken(f"token_{self.get_token_call_count}", int(time.time()) + 3600)
                return default_token
            except Exception as e:
                self.last_exception = e
                raise

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type=None, exc_val=None, exc_tb=None):
        pass


@pytest.mark.asyncio
async def test_background_refresh_triggered_with_sufficient_time():
    """Test that background refresh is triggered when token has sufficient time left until expiration."""
    credential = BackgroundRefreshTestCredential()
    policy = AsyncBearerTokenCredentialPolicy(credential, "https://example.com/.default")

    # Create a token that will trigger background refresh (expires in > 15 minutes)
    expires_in_background_window = int(time.time()) + BACKGROUND_REFRESH_WINDOW_SECONDS + 600  # 25 minutes
    initial_token = AccessToken("initial_token", expires_in_background_window)
    refreshed_token = AccessToken("refreshed_token", int(time.time()) + 3600)

    credential.set_tokens([initial_token, refreshed_token])

    # Set initial token
    policy._token = initial_token

    # Trigger background refresh
    background_started = policy._try_background_refresh()

    assert background_started, "Background refresh should have started with sufficient time left"
    assert policy._background_refresh_task is not None, "Background task should be created"
    assert credential.get_token_call_count == 0, "Token should not be fetched yet"

    # Wait for background task to complete
    if policy._background_refresh_task:
        await policy._background_refresh_task
        # Give a small extra time for any final async operations
        await asyncio.sleep(0.01)

    # Verify token was refreshed in background
    assert credential.get_token_call_count == 1, "Background refresh should have called get_token"
    assert policy._token.token == "refreshed_token", "Token should be updated to refreshed token"


@pytest.mark.asyncio
async def test_background_refresh_not_triggered_when_too_close_to_expiry():
    """Test that background refresh is NOT triggered when token is too close to expiration."""
    credential = BackgroundRefreshTestCredential()
    policy = AsyncBearerTokenCredentialPolicy(credential, "https://example.com/.default")

    # Create a token that's too close to expiry for background refresh (< 15 minutes)
    expires_soon = int(time.time()) + BACKGROUND_REFRESH_WINDOW_SECONDS - 100  # 13.3 minutes
    token_close_to_expiry = AccessToken("expiring_token", expires_soon)

    policy._token = token_close_to_expiry

    # Attempt to trigger background refresh
    background_started = policy._try_background_refresh()

    assert not background_started, "Background refresh should NOT start when token is too close to expiry"
    assert policy._background_refresh_task is None, "No background task should be created"
    assert credential.get_token_call_count == 0, "No token request should be made"


@pytest.mark.asyncio
async def test_background_refresh_uses_existing_token_during_refresh():
    """Test that existing token is used while background refresh is in progress."""
    credential = BackgroundRefreshTestCredential()
    policy = AsyncBearerTokenCredentialPolicy(credential, "https://example.com/.default")

    # Create a slow refreshing credential to test concurrent access
    credential.set_delay(0.2)  # 200ms delay

    expires_in_background_window = int(time.time()) + BACKGROUND_REFRESH_WINDOW_SECONDS + 600
    initial_token = AccessToken("initial_token", expires_in_background_window)
    refreshed_token = AccessToken("refreshed_token", int(time.time()) + 3600)

    credential.set_tokens([initial_token, refreshed_token])
    policy._token = initial_token

    # Mock transport to verify Authorization header
    auth_headers = []

    async def verify_auth_header(request):
        auth_headers.append(request.http_request.headers.get("Authorization"))
        response = Mock()
        response.status_code = 200
        return response

    # Create pipeline
    pipeline = AsyncPipeline(transport=Mock(), policies=[policy, Mock(send=verify_auth_header)])

    # Start background refresh
    background_started = policy._try_background_refresh()
    assert background_started, "Background refresh should start"

    # Make multiple requests while background refresh is running
    await pipeline.run(HttpRequest("GET", "https://example.com/resource1"))
    await pipeline.run(HttpRequest("GET", "https://example.com/resource2"))

    # Wait for background refresh to complete
    if policy._background_refresh_task:
        await policy._background_refresh_task

    # Verify that initial token was used for both requests
    assert len(auth_headers) == 2, "Should have captured 2 auth headers"
    assert all(
        header == "Bearer initial_token" for header in auth_headers
    ), "Should use existing token during background refresh"

    # Verify token was eventually refreshed
    assert policy._token.token == "refreshed_token", "Token should be refreshed after background task completes"


@pytest.mark.asyncio
async def test_background_refresh_handles_credential_failure_gracefully():
    """Test that background refresh failures don't affect existing token usage."""
    credential = BackgroundRefreshTestCredential()
    policy = AsyncBearerTokenCredentialPolicy(credential, "https://example.com/.default")

    # Configure credential to fail during refresh
    credential.set_failure(should_fail=True)

    expires_in_background_window = int(time.time()) + BACKGROUND_REFRESH_WINDOW_SECONDS + 600
    initial_token = AccessToken("initial_token", expires_in_background_window)
    policy._token = initial_token

    # Trigger background refresh
    background_started = policy._try_background_refresh()
    assert background_started, "Background refresh should start even if it will fail"

    # Wait for background task to complete (should handle exception)
    if policy._background_refresh_task:
        await policy._background_refresh_task

    # Verify that existing token is still available and usable
    assert policy._token.token == "initial_token", "Original token should be preserved after background refresh failure"

    # Mock transport to verify the token is still usable
    auth_header = None

    async def capture_auth_header(request):
        nonlocal auth_header
        auth_header = request.http_request.headers.get("Authorization")
        response = Mock()
        response.status_code = 200
        return response

    pipeline = AsyncPipeline(transport=Mock(), policies=[policy, Mock(send=capture_auth_header)])

    # Make a request - should use the original token
    await pipeline.run(HttpRequest("GET", "https://example.com/resource"))

    assert auth_header == "Bearer initial_token", "Should still use original token after failed background refresh"
    assert credential.get_token_call_count == 1, "get_token should have been called once (and failed)"


@pytest.mark.asyncio
async def test_background_refresh_prevents_duplicate_tasks():
    """Test that multiple background refresh attempts don't create duplicate tasks."""
    credential = BackgroundRefreshTestCredential()
    policy = AsyncBearerTokenCredentialPolicy(credential, "https://example.com/.default")

    # Create slow refreshing credential
    credential.set_delay(0.3)  # 300ms delay

    expires_in_background_window = int(time.time()) + BACKGROUND_REFRESH_WINDOW_SECONDS + 600
    initial_token = AccessToken("initial_token", expires_in_background_window)
    refreshed_token = AccessToken("refreshed_token", int(time.time()) + 3600)

    credential.set_tokens([initial_token, refreshed_token])
    policy._token = initial_token

    # First background refresh attempt
    first_started = policy._try_background_refresh()
    assert first_started, "First background refresh should start"
    first_task = policy._background_refresh_task
    assert first_task is not None, "First task should be created"

    # Immediate second attempt should not create new task
    second_started = policy._try_background_refresh()
    assert not second_started, "Second background refresh should not start while first is running"
    assert policy._background_refresh_task is first_task, "Should reuse the same task"

    # Wait for task to complete
    await first_task

    # Verify only one token request was made
    assert credential.get_token_call_count == 1, "Should have made exactly one token request"


@pytest.mark.asyncio
async def test_background_refresh_stale_task_timeout():
    """Test that stale background refresh tasks are cancelled and replaced after 30 seconds."""
    credential = BackgroundRefreshTestCredential()
    policy = AsyncBearerTokenCredentialPolicy(credential, "https://example.com/.default")

    # Set a slow credential to prevent the first task from completing immediately
    credential.set_delay(0.5)

    expires_in_background_window = int(time.time()) + BACKGROUND_REFRESH_WINDOW_SECONDS + 600
    initial_token = AccessToken("initial_token", expires_in_background_window)
    policy._token = initial_token

    # Start first background refresh
    first_started = policy._try_background_refresh()
    assert first_started, "First background refresh should start"
    first_task = policy._background_refresh_task

    # Verify task is running
    assert not first_task.done(), "First task should still be running"

    # Simulate that the first task has been running for more than 30 seconds
    policy._last_background_refresh = time.time() - 35  # 35 seconds ago

    # Second attempt should cancel the stale task and start a new one
    second_started = policy._try_background_refresh()
    assert second_started, "Second background refresh should start after stale timeout"
    second_task = policy._background_refresh_task

    assert second_task is not first_task, "Should create a new task"
    # Give a small moment for cancellation to take effect
    await asyncio.sleep(0.01)
    assert first_task.cancelled(), "First task should be cancelled"

    # Clean up
    if second_task and not second_task.done():
        second_task.cancel()
        try:
            await second_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_background_refresh_no_token_available():
    """Test that background refresh is not triggered when no token is available."""
    credential = BackgroundRefreshTestCredential()
    policy = AsyncBearerTokenCredentialPolicy(credential, "https://example.com/.default")

    # No token set
    assert policy._token is None

    # Attempt background refresh
    background_started = policy._try_background_refresh()

    assert not background_started, "Background refresh should not start without a token"
    assert policy._background_refresh_task is None, "No background task should be created"
    assert credential.get_token_call_count == 0, "No token request should be made"


@pytest.mark.asyncio
async def test_background_refresh_completed_task_cleanup():
    """Test that completed background refresh tasks are properly cleaned up."""
    credential = BackgroundRefreshTestCredential()
    policy = AsyncBearerTokenCredentialPolicy(credential, "https://example.com/.default")

    expires_in_background_window = int(time.time()) + BACKGROUND_REFRESH_WINDOW_SECONDS + 600
    initial_token = AccessToken("initial_token", expires_in_background_window)
    refreshed_token = AccessToken("refreshed_token", int(time.time()) + 3600)

    credential.set_tokens([initial_token, refreshed_token])
    policy._token = initial_token

    # Start and complete background refresh
    first_started = policy._try_background_refresh()
    assert first_started, "Background refresh should start"
    first_task = policy._background_refresh_task

    # Wait for completion
    if first_task:
        await first_task
        assert first_task.done(), "Task should be completed"

    # Next attempt should clean up completed task and start fresh
    second_started = policy._try_background_refresh()
    assert second_started, "Second background refresh should start"
    second_task = policy._background_refresh_task

    assert second_task is not first_task, "Should create a new task after cleanup"

    # Clean up
    if second_task and not second_task.done():
        second_task.cancel()
        try:
            await second_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("http_request", HTTP_REQUESTS)
async def test_background_refresh_integration_with_on_request(http_request):
    """Test that background refresh integrates properly with the on_request flow when token needs refresh."""

    # Create a custom token class that can have refresh_on attribute
    class RefreshOnToken:
        def __init__(self, token, expires_on, refresh_on):
            self.token = token
            self.expires_on = expires_on
            self.refresh_on = refresh_on

    credential = BackgroundRefreshTestCredential()
    policy = AsyncBearerTokenCredentialPolicy(credential, "https://example.com/.default")

    # Create a token that needs refresh (refresh_on has passed) but has significant time left
    now = time.time()
    expires_in_background_window = int(now + BACKGROUND_REFRESH_WINDOW_SECONDS + 600)
    refresh_on_passed = int(now - 100)  # refresh_on time has already passed

    # Use our custom token class with refresh_on in the past
    initial_token = RefreshOnToken("initial_token", expires_in_background_window, refresh_on_passed)
    refreshed_token = AccessToken("refreshed_token", int(now + 3600))

    credential.set_tokens([refreshed_token])

    # Set initial token that needs refresh
    policy._token = initial_token  # type: ignore

    # Create request
    request = PipelineRequest(http_request("GET", "https://example.com"), PipelineContext(None))

    # Call on_request - should trigger background refresh because refresh_on has passed
    # but token still has significant time left
    await policy.on_request(request)

    # Verify Authorization header uses existing token (background refresh doesn't block)
    assert request.http_request.headers["Authorization"] == "Bearer initial_token"

    # Verify background refresh was started
    assert policy._background_refresh_task is not None, "Background refresh should be started"

    # Wait for background refresh to complete
    await policy._background_refresh_task

    # Verify token was refreshed
    assert policy._token.token == "refreshed_token", "Token should be refreshed in background"  # type: ignore
    assert credential.get_token_call_count == 1, "get_token should be called once for background refresh"


@pytest.mark.asyncio
async def test_background_refresh_with_refresh_on_token():
    """Test background refresh behavior with tokens that have refresh_on attribute."""

    # Create a custom token class that can have refresh_on attribute
    class RefreshOnToken:
        def __init__(self, token, expires_on, refresh_on):
            self.token = token
            self.expires_on = expires_on
            self.refresh_on = refresh_on

    credential = BackgroundRefreshTestCredential()
    policy = AsyncBearerTokenCredentialPolicy(credential, "https://example.com/.default")

    # Create token with refresh_on that has passed, but with significant time left
    now = time.time()
    expires_in_background_window = int(now + BACKGROUND_REFRESH_WINDOW_SECONDS + 600)
    refresh_on_passed = int(now - 100)  # refresh_on time has already passed

    # Use our custom token class
    initial_token = RefreshOnToken("initial_token", expires_in_background_window, refresh_on_passed)
    policy._token = initial_token  # type: ignore
    credential.set_tokens([AccessToken("refreshed_token", expires_in_background_window + 100)])

    # Create proper pipeline request
    request = HttpRequest("GET", "https://example.com")
    pipeline_request = PipelineRequest(request, PipelineContext(None))

    # Call on_request - should trigger background refresh because refresh_on has passed
    # and token still has significant time left (>15 minutes)
    await policy.on_request(pipeline_request)

    # Background refresh should have been started
    assert policy._background_refresh_task is not None
    assert not policy._background_refresh_task.done()

    # Wait for background refresh to complete
    await asyncio.sleep(0.1)

    # Verify token was refreshed
    assert policy._token.token == "refreshed_token"  # type: ignore
    assert credential.get_token_call_count == 1


@pytest.mark.asyncio
async def test_background_refresh_not_triggered_when_token_fresh():
    """Test that background refresh is not triggered when token doesn't need refresh."""
    credential = BackgroundRefreshTestCredential()
    policy = AsyncBearerTokenCredentialPolicy(credential, "https://example.com/.default")

    # Create token that does NOT need refresh (no refresh_on, expires far in future)
    now = time.time()
    expires_in_background_window = int(now + BACKGROUND_REFRESH_WINDOW_SECONDS + 600)

    initial_token = AccessToken("initial_token", expires_in_background_window)
    policy._token = initial_token
    credential.set_tokens([AccessToken("refreshed_token", expires_in_background_window + 100)])

    # Create proper pipeline request
    request = HttpRequest("GET", "https://example.com")
    pipeline_request = PipelineRequest(request, PipelineContext(None))

    # Call on_request - should NOT trigger background refresh because token doesn't need refresh
    await policy.on_request(pipeline_request)

    # Background refresh should not have been started
    assert policy._background_refresh_task is None
    assert credential.get_token_call_count == 0

    # Verify authorization header was set with original token
    assert request.headers["Authorization"] == "Bearer initial_token"
