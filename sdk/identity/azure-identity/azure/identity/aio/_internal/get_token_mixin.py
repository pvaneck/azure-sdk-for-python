# ------------------------------------
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# ------------------------------------
import abc
import asyncio
import logging
import threading
import time
from typing import Any, Optional, Dict, Type, Tuple
from weakref import WeakValueDictionary

from azure.core.credentials import AccessToken, AccessTokenInfo, TokenRequestOptions
from ..._constants import DEFAULT_REFRESH_OFFSET, DEFAULT_TOKEN_REFRESH_RETRY_DELAY
from ..._internal import within_credential_chain
from .utils import get_running_async_lock_class

_LOGGER = logging.getLogger(__name__)


def _get_current_context_id() -> int:
    """Get a unique identifier for the current async context.

    For asyncio, this returns the event loop ID.
    For Trio, this returns the current Trio token ID (representing the Trio run context).
    """
    # Try asyncio first since it's more common
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        pass

    # Try Trio - if we can get a current_trio_token, we're in Trio
    try:
        import trio

        token = trio.lowlevel.current_trio_token()
        return id(token)
    except ImportError:
        pass
    except RuntimeError:
        # Not running in a Trio context
        pass

    return 0


class GetTokenMixin(abc.ABC):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._last_request_time = 0

        # Thread lock to protect access to per-loop state
        self._thread_lock = threading.Lock()
        # Maps event loop/Trio context id -> (global_lock, active_locks WeakValueDict)
        # Each event loop/Trio context gets its own set of locks since async locks are bound to their context
        self._per_loop_state: Dict[int, Tuple[Any, WeakValueDictionary]] = {}
        self._lock_class_type: Optional[Type] = None

        # https://github.com/python/mypy/issues/5887
        super(GetTokenMixin, self).__init__(*args, **kwargs)  # type: ignore

    @property
    def _lock_class(self) -> Type:
        if self._lock_class_type is None:
            self._lock_class_type = get_running_async_lock_class()
        return self._lock_class_type

    def _get_loop_state(self, context_id: int) -> Tuple[Any, WeakValueDictionary]:
        """Get or create lock state for the given event loop or Trio context.

        This should be called while holding _thread_lock.
        """
        if context_id not in self._per_loop_state:
            global_lock = self._lock_class()
            active_locks: WeakValueDictionary = WeakValueDictionary()
            self._per_loop_state[context_id] = (global_lock, active_locks)
        return self._per_loop_state[context_id]

    async def _get_request_lock(self, lock_key: tuple) -> Any:
        """Get or create a lock for the given key, specific to the current event loop or Trio context.

        Each event loop/Trio context gets its own set of locks because async Lock objects are
        bound to the context in which they were created.
        """
        context_id = _get_current_context_id()

        with self._thread_lock:
            global_lock, active_locks = self._get_loop_state(context_id)

            # Check for existing lock
            lock = active_locks.get(lock_key)
            if lock is not None:
                return lock

        # Need to create new lock - acquire global lock first (outside thread lock)
        async with global_lock:
            with self._thread_lock:
                # Re-get state in case it was modified
                _, active_locks = self._get_loop_state(context_id)

                # Double-check in case another coroutine created it while we waited
                lock = active_locks.get(lock_key)
                if lock is None:
                    lock = self._lock_class()
                    active_locks[lock_key] = lock
                return lock

    @abc.abstractmethod
    async def _acquire_token_silently(self, *scopes: str, **kwargs) -> Optional[AccessTokenInfo]:
        """Attempt to acquire an access token from a cache or by redeeming a refresh token.

        :param str scopes: desired scopes for the access token. This method requires at least one scope.
            For more information about scopes, see
            https://learn.microsoft.com/entra/identity-platform/scopes-oidc.

        :return: An access token with the desired scopes if successful; otherwise, None.
        :rtype: ~azure.core.credentials.AccessTokenInfo or None
        """

    @abc.abstractmethod
    async def _request_token(self, *scopes: str, **kwargs) -> AccessTokenInfo:
        """Request an access token from the STS.

        :param str scopes: desired scopes for the access token. This method requires at least one scope.
            For more information about scopes, see
            https://learn.microsoft.com/entra/identity-platform/scopes-oidc.

        :return: An access token with the desired scopes.
        :rtype: ~azure.core.credentials.AccessTokenInfo
        """

    def _should_refresh(self, token: AccessTokenInfo) -> bool:
        now = int(time.time())
        if now >= token.expires_on:
            return True
        if token.refresh_on is not None and now >= token.refresh_on:
            return True
        if token.expires_on - now > DEFAULT_REFRESH_OFFSET:
            return False
        if now - self._last_request_time < DEFAULT_TOKEN_REFRESH_RETRY_DELAY:
            return False
        return True

    async def get_token(
        self,
        *scopes: str,
        claims: Optional[str] = None,
        tenant_id: Optional[str] = None,
        enable_cae: bool = False,
        **kwargs: Any,
    ) -> AccessToken:
        """Request an access token for `scopes`.

        This method is called automatically by Azure SDK clients.

        :param str scopes: desired scopes for the access token. This method requires at least one scope.
            For more information about scopes, see
            https://learn.microsoft.com/entra/identity-platform/scopes-oidc.
        :keyword str claims: additional claims required in the token, such as those returned in a resource provider's
            claims challenge following an authorization failure.
        :keyword str tenant_id: optional tenant to include in the token request.
        :keyword bool enable_cae: indicates whether to enable Continuous Access Evaluation (CAE) for the requested
            token. Defaults to False.

        :return: An access token with the desired scopes.
        :rtype: ~azure.core.credentials.AccessToken
        :raises CredentialUnavailableError: the credential is unable to attempt authentication because it lacks
            required data, state, or platform support
        :raises ~azure.core.exceptions.ClientAuthenticationError: authentication failed. The error's ``message``
            attribute gives a reason.
        """
        options: TokenRequestOptions = {}
        if claims:
            options["claims"] = claims
        if tenant_id:
            options["tenant_id"] = tenant_id
        options["enable_cae"] = enable_cae

        token_info = await self._get_token_base(*scopes, options=options, base_method_name="get_token", **kwargs)
        return AccessToken(token_info.token, token_info.expires_on)

    async def get_token_info(self, *scopes: str, options: Optional[TokenRequestOptions] = None) -> AccessTokenInfo:
        """Request an access token for `scopes`.

        This is an alternative to `get_token` to enable certain scenarios that require additional properties
        on the token. This method is called automatically by Azure SDK clients.

        :param str scopes: desired scopes for the access token. This method requires at least one scope.
            For more information about scopes, see https://learn.microsoft.com/entra/identity-platform/scopes-oidc.
        :keyword options: A dictionary of options for the token request. Unknown options will be ignored. Optional.
        :paramtype options: ~azure.core.credentials.TokenRequestOptions

        :rtype: ~azure.core.credentials.AccessTokenInfo
        :return: An AccessTokenInfo instance containing information about the token.
        :raises CredentialUnavailableError: the credential is unable to attempt authentication because it lacks
            required data, state, or platform support
        :raises ~azure.core.exceptions.ClientAuthenticationError: authentication failed. The error's ``message``
            attribute gives a reason.
        """
        return await self._get_token_base(*scopes, options=options, base_method_name="get_token_info")

    async def _get_token_base(
        self,
        *scopes: str,
        options: Optional[TokenRequestOptions] = None,
        base_method_name: str = "get_token_info",
        **kwargs: Any,
    ) -> AccessTokenInfo:
        if not scopes:
            raise ValueError(f'"{base_method_name}" requires at least one scope')

        options = options or {}
        claims = options.get("claims")
        tenant_id = options.get("tenant_id")
        enable_cae = options.get("enable_cae", False)

        try:
            token = await self._acquire_token_silently(
                *scopes, claims=claims, tenant_id=tenant_id, enable_cae=enable_cae, **kwargs
            )
            if not token or self._should_refresh(token):
                # Get the lock specific to this scope combination
                lock_key = (tuple(sorted(scopes)), claims, tenant_id, enable_cae)
                lock = await self._get_request_lock(lock_key)

                async with lock:
                    # Double-check in case another coroutine refreshed the token while we waited for the lock
                    current_token = await self._acquire_token_silently(
                        *scopes, claims=claims, tenant_id=tenant_id, enable_cae=enable_cae, **kwargs
                    )
                    if current_token and not self._should_refresh(current_token):
                        token = current_token
                    else:
                        try:
                            token = await self._request_token(
                                *scopes, claims=claims, tenant_id=tenant_id, enable_cae=enable_cae, **kwargs
                            )
                        except Exception:  # pylint:disable=broad-except
                            self._last_request_time = int(time.time())
                            # Only raise if we don't have a valid (non-expired) token to return
                            if current_token is None or current_token.expires_on <= self._last_request_time:
                                raise
                            token = current_token

            _LOGGER.log(
                logging.DEBUG if within_credential_chain.get() else logging.INFO,
                "%s.%s succeeded",
                self.__class__.__name__,
                base_method_name,
            )
            return token

        except Exception as ex:
            _LOGGER.log(
                logging.DEBUG if within_credential_chain.get() else logging.WARNING,
                "%s.%s failed: %s",
                self.__class__.__name__,
                base_method_name,
                ex,
                exc_info=_LOGGER.isEnabledFor(logging.DEBUG),
            )
            raise

    def __getstate__(self) -> Dict[str, Any]:
        state = self.__dict__.copy()
        # Remove the non-picklable entries (locks and threading primitives)
        del state["_thread_lock"]
        del state["_per_loop_state"]
        del state["_lock_class_type"]
        return state

    def __setstate__(self, state: Dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._thread_lock = threading.Lock()
        self._per_loop_state = {}
        self._lock_class_type = None
