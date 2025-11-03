# ------------------------------------
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# ------------------------------------
import functools
import asyncio
from unittest.mock import Mock, patch, MagicMock
from urllib.parse import urlparse

from azure.core.exceptions import ClientAuthenticationError, ServiceRequestError
from azure.core.pipeline.transport import HttpRequest
from azure.identity._constants import EnvironmentVariables
from azure.identity._internal import AadClientCertificate
from azure.identity._internal.utils import create_request_key
from azure.identity.aio._internal.aad_client import AadClient
from msal import TokenCache
import pytest

from helpers import build_aad_response, mock_response
from helpers_async import get_completed_future
from test_certificate_credential import PEM_CERT_PATH

pytestmark = pytest.mark.asyncio


async def test_error_reporting():
    error_name = "everything's sideways"
    error_description = "something went wrong"
    error_response = {"error": error_name, "error_description": error_description}

    response = mock_response(status_code=403, json_payload=error_response)

    async def send(*_, **__):
        return response

    transport = Mock(send=Mock(wraps=send))
    client = AadClient("tenant id", "client id", transport=transport)

    fns = [
        functools.partial(client.obtain_token_by_authorization_code, ("scope",), "code", "uri"),
        functools.partial(client.obtain_token_by_refresh_token, ("scope",), "refresh token"),
    ]

    # exceptions raised for Microsoft Entra errors should contain Microsoft Entra's error description
    for fn in fns:
        with pytest.raises(ClientAuthenticationError) as ex:
            await fn()
        message = str(ex.value)
        assert error_name in message and error_description in message
        assert transport.send.call_count == 1
        transport.send.reset_mock()


@pytest.mark.skip(reason="Adding body to HttpResponseError str. Not an issue bc we don't automatically log errors")
async def test_exceptions_do_not_expose_secrets():
    secret = "secret"
    body = {"error": "bad thing", "access_token": secret, "refresh_token": secret}
    response = mock_response(status_code=403, json_payload=body)

    async def send(*_, **__):
        return response

    transport = Mock(send=Mock(wraps=send))

    client = AadClient("tenant id", "client id", transport=transport)

    fns = [
        functools.partial(client.obtain_token_by_authorization_code, "code", "uri", ("scope",)),
        functools.partial(client.obtain_token_by_refresh_token, "refresh token", ("scope",)),
    ]

    async def assert_secrets_not_exposed():
        for fn in fns:
            with pytest.raises(ClientAuthenticationError) as ex:
                await fn()
            assert secret not in str(ex.value)
            assert secret not in repr(ex.value)
            assert transport.send.call_count == 1
            transport.send.reset_mock()

    # Microsoft Entra errors shouldn't provoke exceptions exposing secrets
    await assert_secrets_not_exposed()

    # neither should unexpected Microsoft Entra responses
    del body["error"]
    await assert_secrets_not_exposed()


@pytest.mark.parametrize("secret", (None, "client secret"))
async def test_authorization_code(secret):
    tenant_id = "tenant-id"
    client_id = "client-id"
    auth_code = "code"
    scope = "scope"
    redirect_uri = "https://localhost"
    access_token = "***"

    async def send(request, **_):
        assert request.data["client_id"] == client_id
        assert request.data["code"] == auth_code
        assert request.data["grant_type"] == "authorization_code"
        assert request.data["redirect_uri"] == redirect_uri
        assert request.data["scope"] == scope
        assert request.data.get("client_secret") == secret

        return mock_response(json_payload={"access_token": access_token, "expires_in": 42})

    transport = Mock(send=Mock(wraps=send))

    client = AadClient(tenant_id, client_id, transport=transport)
    token = await client.obtain_token_by_authorization_code(
        scopes=(scope,), code=auth_code, redirect_uri=redirect_uri, client_secret=secret
    )

    assert token.token == access_token
    assert transport.send.call_count == 1


async def test_client_secret():
    tenant_id = "tenant-id"
    client_id = "client-id"
    scope = "scope"
    secret = "refresh-token"
    access_token = "***"

    async def send(request, **_):
        assert request.data["client_id"] == client_id
        assert request.data["client_secret"] == secret
        assert request.data["grant_type"] == "client_credentials"
        assert request.data["scope"] == scope

        return mock_response(json_payload={"access_token": access_token, "expires_in": 42})

    transport = Mock(send=Mock(wraps=send))

    client = AadClient(tenant_id, client_id, transport=transport)
    token = await client.obtain_token_by_client_secret(scopes=(scope,), secret=secret)

    assert token.token == access_token
    assert transport.send.call_count == 1


async def test_refresh_token():
    tenant_id = "tenant-id"
    client_id = "client-id"
    scope = "scope"
    refresh_token = "refresh-token"
    access_token = "***"

    async def send(request, **_):
        assert request.data["client_id"] == client_id
        assert request.data["grant_type"] == "refresh_token"
        assert request.data["refresh_token"] == refresh_token
        assert request.data["scope"] == scope

        return mock_response(json_payload={"access_token": access_token, "expires_in": 42})

    transport = Mock(send=Mock(wraps=send))

    client = AadClient(tenant_id, client_id, transport=transport)
    token = await client.obtain_token_by_refresh_token(scopes=(scope,), refresh_token=refresh_token)

    assert token.token == access_token
    assert transport.send.call_count == 1


@pytest.mark.parametrize("authority", ("localhost", "https://localhost"))
async def test_request_url(authority):
    tenant_id = "expected-tenant"
    parsed_authority = urlparse(authority)
    expected_netloc = parsed_authority.netloc or authority  # "localhost" parses to netloc "", path "localhost"

    async def send(request, **_):
        actual = urlparse(request.url)
        assert actual.scheme == "https"
        assert actual.netloc == expected_netloc
        assert actual.path.startswith("/" + tenant_id)
        return mock_response(json_payload={"token_type": "Bearer", "expires_in": 42, "access_token": "***"})

    client = AadClient(tenant_id, "client id", transport=Mock(send=send), authority=authority)

    await client.obtain_token_by_authorization_code("scope", "code", "uri")
    await client.obtain_token_by_refresh_token("scope", "refresh token")

    # obtain_token_by_refresh_token is client_secret safe
    await client.obtain_token_by_refresh_token("scope", "refresh token", client_secret="secret")

    # authority can be configured via environment variable
    with patch.dict("os.environ", {EnvironmentVariables.AZURE_AUTHORITY_HOST: authority}, clear=True):
        client = AadClient(tenant_id=tenant_id, client_id="client id", transport=Mock(send=send))
    await client.obtain_token_by_authorization_code("scope", "code", "uri")
    await client.obtain_token_by_refresh_token("scope", "refresh token")


async def test_evicts_invalid_refresh_token():
    """when Microsoft Entra ID rejects a refresh token, the client should evict that token from its cache"""

    tenant_id = "tenant-id"
    client_id = "client-id"
    invalid_token = "invalid-refresh-token"

    cache = TokenCache()
    cache.add({"response": build_aad_response(uid="id1", utid="tid1", access_token="*", refresh_token=invalid_token)})
    cache.add({"response": build_aad_response(uid="id2", utid="tid2", access_token="*", refresh_token="...")})
    assert len(list(cache.search(TokenCache.CredentialType.REFRESH_TOKEN))) == 2
    assert len(list(cache.search(TokenCache.CredentialType.REFRESH_TOKEN, query={"secret": invalid_token}))) == 1

    async def send(request, **_):
        assert request.data["refresh_token"] == invalid_token
        return mock_response(json_payload={"error": "invalid_grant"}, status_code=400)

    transport = Mock(send=Mock(wraps=send))

    client = AadClient(tenant_id, client_id, transport=transport, cache=cache)
    with pytest.raises(ClientAuthenticationError):
        await client.obtain_token_by_refresh_token(scopes=("scope",), refresh_token=invalid_token)

    assert transport.send.call_count == 1
    assert len(list(cache.search(TokenCache.CredentialType.REFRESH_TOKEN))) == 1
    assert len(list(cache.search(TokenCache.CredentialType.REFRESH_TOKEN, query={"secret": invalid_token}))) == 0


async def test_retries_token_requests():
    """The client should retry token requests"""

    message = "can't connect"
    transport = Mock(send=Mock(side_effect=ServiceRequestError(message)), sleep=get_completed_future)
    client = AadClient("tenant-id", "client-id", transport=transport)

    with pytest.raises(ServiceRequestError, match=message):
        await client.obtain_token_by_authorization_code("", "", "")
    assert transport.send.call_count > 1
    transport.send.reset_mock()

    with pytest.raises(ServiceRequestError, match=message):
        await client.obtain_token_by_client_certificate("", AadClientCertificate(open(PEM_CERT_PATH, "rb").read()))
    assert transport.send.call_count > 1
    transport.send.reset_mock()

    with pytest.raises(ServiceRequestError, match=message):
        await client.obtain_token_by_client_secret("", "")
    assert transport.send.call_count > 1
    transport.send.reset_mock()

    with pytest.raises(ServiceRequestError, match=message):
        await client.obtain_token_by_jwt_assertion("", "")
    assert transport.send.call_count > 1
    transport.send.reset_mock()

    with pytest.raises(ServiceRequestError, match=message):
        await client.obtain_token_by_refresh_token("", "")
    assert transport.send.call_count > 1


async def test_shared_cache():
    """The client should return only tokens associated with its own client_id"""

    client_id_a = "client-id-a"
    client_id_b = "client-id-b"
    scope = "scope"
    expected_token = "***"
    tenant_id = "tenant"
    authority = "https://localhost/" + tenant_id

    cache = TokenCache()
    cache.add(
        {
            "response": build_aad_response(access_token=expected_token),
            "client_id": client_id_a,
            "scope": [scope],
            "token_endpoint": "/".join((authority, tenant_id, "oauth2/v2.0/token")),
        }
    )

    common_args = dict(authority=authority, cache=cache, tenant_id=tenant_id)
    client_a = AadClient(client_id=client_id_a, **common_args)
    client_b = AadClient(client_id=client_id_b, **common_args)

    # A has a cached token
    token = client_a.get_cached_access_token([scope])
    assert token.token == expected_token

    # which B shouldn't return
    assert client_b.get_cached_access_token([scope]) is None


async def test_multitenant_cache():
    client_id = "client-id"
    scope = "scope"
    expected_token = "***"
    tenant_a = "tenant-a"
    tenant_b = "tenant-b"
    tenant_c = "tenant-c"
    tenant_d = "tenant-d"
    authority = "https://localhost/" + tenant_a
    message = "additionally_allowed_tenants"

    cache = TokenCache()
    cache.add(
        {
            "response": build_aad_response(access_token=expected_token),
            "client_id": client_id,
            "scope": [scope],
            "token_endpoint": "/".join((authority, tenant_a, "oauth2/v2.0/token")),
        }
    )

    common_args = dict(authority=authority, cache=cache, client_id=client_id)
    client_a = AadClient(tenant_id=tenant_a, **common_args)
    client_b = AadClient(tenant_id=tenant_b, **common_args)

    # A has a cached token
    token = client_a.get_cached_access_token([scope])
    assert token.token == expected_token

    # which B shouldn't return
    assert client_b.get_cached_access_token([scope]) is None

    # but C allows multitenant auth and should therefore return the token from tenant_a when appropriate
    client_c = AadClient(tenant_id=tenant_c, additionally_allowed_tenants=["*"], **common_args)
    assert client_c.get_cached_access_token([scope]) is None
    token = client_c.get_cached_access_token([scope], tenant_id=tenant_a)
    assert token.token == expected_token

    # but d does not add target tenant into allowed list therefore fail
    client_d = AadClient(tenant_id=tenant_d, **common_args)
    assert client_d.get_cached_access_token([scope]) is None
    with pytest.raises(ClientAuthenticationError, match=message):
        client_d.get_cached_access_token([scope], tenant_id=tenant_a)


async def test_request_coalescing_single_request():
    """Test that a single async request proceeds normally without coalescing."""
    tenant_id = "tenant-id"
    client_id = "client-id"
    scope = "scope"
    secret = "client-secret"
    access_token = "access-token"

    async def send(request, **_):
        return mock_response(json_payload={"access_token": access_token, "expires_in": 42})

    transport = Mock(send=Mock(wraps=send))
    client = AadClient(tenant_id, client_id, transport=transport)

    token = await client.obtain_token_by_client_secret(scopes=(scope,), secret=secret)

    assert token.token == access_token
    assert transport.send.call_count == 1


async def test_request_coalescing_identical_requests():
    """Test that identical concurrent async requests are coalesced into a single network call."""
    tenant_id = "tenant-id"
    client_id = "client-id"
    scope = "scope"
    secret = "client-secret"
    access_token = "access-token"

    async def send(request, **_):
        # Add a delay to simulate network latency
        await asyncio.sleep(0.2)
        return mock_response(json_payload={"access_token": access_token, "expires_in": 42})

    transport = Mock(send=Mock(wraps=send))
    client = AadClient(tenant_id, client_id, transport=transport)

    # Create multiple coroutines making the same request
    async def make_request():
        return await client.obtain_token_by_client_secret(scopes=(scope,), secret=secret)

    # Run 5 concurrent requests
    results = await asyncio.gather(*[make_request() for _ in range(5)])

    # Verify all requests returned the same token
    assert len(results) == 5
    for token in results:
        assert token.token == access_token

    # Verify only one network call was made despite 5 concurrent requests
    assert transport.send.call_count == 1


async def test_request_coalescing_different_requests():
    """Test that different async requests are not coalesced."""
    tenant_id = "tenant-id"
    client_id = "client-id"
    scope1 = "scope1"
    scope2 = "scope2"
    secret = "client-secret"
    access_token = "access-token"

    async def send(request, **_):
        return mock_response(json_payload={"access_token": access_token, "expires_in": 42})

    transport = Mock(send=Mock(wraps=send))
    client = AadClient(tenant_id, client_id, transport=transport)

    # Create coroutines making different requests
    async def make_request1():
        return await client.obtain_token_by_client_secret(scopes=(scope1,), secret=secret)

    async def make_request2():
        return await client.obtain_token_by_client_secret(scopes=(scope2,), secret=secret)

    # Run concurrent requests with different scopes
    tasks = [make_request1() for _ in range(3)] + [make_request2() for _ in range(3)]
    results = await asyncio.gather(*tasks)

    # Verify all requests returned tokens
    assert len(results) == 6
    for token in results:
        assert token.token == access_token

    assert transport.send.call_count == 2


async def test_request_coalescing_sequential_requests():
    """Test that sequential async requests (not concurrent) each make their own network call."""
    tenant_id = "tenant-id"
    client_id = "client-id"
    scope = "scope"
    secret = "client-secret"
    access_token = "access-token"

    async def send(request, **_):
        return mock_response(json_payload={"access_token": access_token, "expires_in": 42})

    transport = Mock(send=Mock(wraps=send))
    client = AadClient(tenant_id, client_id, transport=transport)

    # Make three sequential requests
    token1 = await client.obtain_token_by_client_secret(scopes=(scope,), secret=secret)
    token2 = await client.obtain_token_by_client_secret(scopes=(scope,), secret=secret)
    token3 = await client.obtain_token_by_client_secret(scopes=(scope,), secret=secret)

    # Verify all requests returned tokens
    assert token1.token == access_token
    assert token2.token == access_token
    assert token3.token == access_token

    # Verify three separate network calls were made
    assert transport.send.call_count == 3


async def test_request_coalescing_cleanup_on_close():
    """Test that async client close method works correctly."""
    tenant_id = "tenant-id"
    client_id = "client-id"
    scope = "scope"
    secret = "client-secret"

    async def send(request, **_):
        # Simulate a long-running request
        await asyncio.sleep(0.5)
        return mock_response(json_payload={"access_token": "token", "expires_in": 42})

    transport = MagicMock(send=Mock(wraps=send))
    client = AadClient(tenant_id, client_id, transport=transport)

    # Start a request as a background task
    async def make_request():
        return await client.obtain_token_by_client_secret(scopes=(scope,), secret=secret)

    task = asyncio.create_task(make_request())

    # Give the request time to start
    await asyncio.sleep(0.1)

    # Close the client while request is in progress
    await client.close()

    # Cancel the task since client is closed
    task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        pass  # Expected

    assert not client._pending_requests


async def test_request_coalescing_different_methods():
    """Test that different async credential methods with same parameters are not coalesced."""
    tenant_id = "tenant-id"
    client_id = "client-id"
    scope = "scope"
    secret = "client-secret"
    refresh_token = "refresh-token"
    access_token = "access-token"

    async def send(request, **_):
        return mock_response(json_payload={"access_token": access_token, "expires_in": 42})

    transport = Mock(send=Mock(wraps=send))
    client = AadClient(tenant_id, client_id, transport=transport)

    # Create coroutines for different credential methods
    async def make_client_secret_request():
        return await client.obtain_token_by_client_secret(scopes=(scope,), secret=secret)

    async def make_refresh_token_request():
        return await client.obtain_token_by_refresh_token(scopes=(scope,), refresh_token=refresh_token)

    # Run both types of requests concurrently
    results = await asyncio.gather(make_client_secret_request(), make_refresh_token_request())

    # Verify both requests returned tokens
    assert len(results) == 2
    for token in results:
        assert token.token == access_token

    # Verify two separate network calls were made (different request bodies)
    assert transport.send.call_count == 2


async def test_async_request_key_creation():
    """Test that async request keys are created correctly for deduplication."""

    # Test requests with same URL and body produce same key
    request1 = HttpRequest("POST", "https://example.com/token")
    request1.body = {"grant_type": "client_credentials", "scope": "scope"}

    request2 = HttpRequest("POST", "https://example.com/token")
    request2.body = {"grant_type": "client_credentials", "scope": "scope"}

    key1 = create_request_key(request1)
    key2 = create_request_key(request2)

    assert key1 == key2

    # Test requests with different URLs produce different keys
    request3 = HttpRequest("POST", "https://different.com/token")
    request3.body = {"grant_type": "client_credentials", "scope": "scope"}

    key3 = create_request_key(request3)
    assert key1 != key3

    # Test requests with different bodies produce different keys
    request4 = HttpRequest("POST", "https://example.com/token")
    request4.body = {"grant_type": "client_credentials", "scope": "different_scope"}

    key4 = create_request_key(request4)
    assert key1 != key4


async def test_request_coalescing_with_context_manager():
    """Test that request coalescing works with async context manager."""
    tenant_id = "tenant-id"
    client_id = "client-id"
    scope = "scope"
    secret = "client-secret"
    access_token = "access-token"

    async def send(request, **_):
        await asyncio.sleep(0.1)
        return mock_response(json_payload={"access_token": access_token, "expires_in": 42})

    transport = MagicMock(send=Mock(wraps=send))

    async with AadClient(tenant_id, client_id, transport=transport) as client:
        # Create multiple coroutines making the same request
        async def make_request():
            return await client.obtain_token_by_client_secret(scopes=(scope,), secret=secret)

        # Run 3 concurrent requests
        results = await asyncio.gather(*[make_request() for _ in range(3)])

        # Verify all requests returned the same token
        assert len(results) == 3
        for token in results:
            assert token.token == access_token

        # Verify only one network call was made
        assert transport.send.call_count == 1
