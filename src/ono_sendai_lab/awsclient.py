"""Managed AWS client adapter, used only inside the sandbox (LAB-024, LAB-025).

Every client is built from an explicit botocore session with explicit
emulator keys, the run CA, one endpoint and one region. Shared config and
credential files point at a nonexistent path, metadata discovery is off,
proxy variables are removed, and a ``before-send`` guard refuses any request
whose URL is not on the emulator endpoint (no redirect, no default-endpoint
fallback, ever).
"""

from __future__ import annotations

import datetime
import os

from .errors import LabError
from .redact import redact_obj, redact_text

NONEXISTENT = "/nonexistent/ono-sendai-lab/aws-config"
_STRIP_ENV = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_ENDPOINT_URL",
    "AWS_CA_BUNDLE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_ROLE_ARN",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)


class EndpointGuardError(Exception):
    """Raised before sending when a request leaves the emulator endpoint."""


def harden_environment() -> None:
    for name in _STRIP_ENV:
        os.environ.pop(name, None)
    os.environ["AWS_CONFIG_FILE"] = NONEXISTENT
    os.environ["AWS_SHARED_CREDENTIALS_FILE"] = NONEXISTENT
    os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
    os.environ["AWS_DISABLE_HOST_PREFIX_INJECTION"] = "true"


def _normalize(value):
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items() if k != "ResponseMetadata"}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value


class ManagedClient:
    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        ca_file: str,
        access_key: str,
        secret_key: str,
        connect_timeout: float = 5.0,
        read_timeout: float = 20.0,
    ):
        harden_environment()
        import botocore.session
        from botocore.config import Config

        self.endpoint = endpoint.rstrip("/")
        self.region = region
        self._ca_file = ca_file
        self._keys = (access_key, secret_key)
        self._session = botocore.session.Session()
        self._session.set_config_variable("region", region)
        self._clients: dict[tuple[str, bool], object] = {}
        self._config_cls = Config
        self._timeouts = (connect_timeout, read_timeout)

    def _guard(self, request, **_kwargs):
        url = getattr(request, "url", "")
        if not (url == self.endpoint or url.startswith(self.endpoint + "/") or url.startswith(self.endpoint + "?")):
            raise EndpointGuardError("request left the emulator endpoint")

    def client(
        self, service: str, mutating: bool, *, ca_file: str | None = None, keys: tuple[str, str] | None = None, endpoint: str | None = None
    ):
        """Return a cached client; overrides exist only for negative probes."""
        cacheable = ca_file is None and keys is None and endpoint is None
        key = (service, mutating)
        if cacheable and key in self._clients:
            return self._clients[key]
        config = self._config_cls(
            region_name=self.region,
            connect_timeout=self._timeouts[0],
            read_timeout=self._timeouts[1],
            retries={"total_max_attempts": 1 if mutating else 3, "mode": "standard"},
            inject_host_prefix=False,
            proxies={},
            s3={"addressing_style": "path"},
            signature_version="s3v4" if service == "s3" else None,
            user_agent_appid="ono-sendai-lab",
        )
        access, secret = keys or self._keys
        client = self._session.create_client(
            service,
            region_name=self.region,
            endpoint_url=endpoint or self.endpoint,
            verify=ca_file or self._ca_file,
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            config=config,
        )
        guard_endpoint = (endpoint or self.endpoint).rstrip("/")
        if guard_endpoint == self.endpoint:
            client.meta.events.register_first("before-send", self._guard)
        if cacheable:
            self._clients[key] = client
        return client

    def call(self, service: str, api_name: str, params: dict, *, mutating: bool, **overrides) -> dict:
        import botocore.exceptions as bex

        client = self.client(service, mutating, **overrides)
        method = getattr(client, _snake(api_name))
        try:
            response = method(**params)
        except EndpointGuardError as exc:
            raise LabError("unsafe_configuration", "request blocked: it would leave the emulator endpoint") from exc
        except bex.ClientError as exc:
            err = exc.response.get("Error", {})
            raise LabError(
                "aws_error",
                f"{service} {api_name} failed: {err.get('Code', 'Unknown')}",
                {
                    "service": service,
                    "operation": api_name,
                    "aws_code": err.get("Code"),
                    "http_status": exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode"),
                    "aws_message": redact_text(str(err.get("Message", ""))[:500]),
                },
            ) from None
        except bex.SSLError as exc:
            raise LabError(
                "transport_error", f"{service} {api_name}: TLS verification failed", {"reason": "tls_verification_failed"}
            ) from exc
        except (bex.ConnectTimeoutError, bex.ReadTimeoutError) as exc:
            raise LabError("timeout", f"{service} {api_name}: timed out", {"reason": type(exc).__name__}) from exc
        except bex.EndpointConnectionError as exc:
            raise LabError("transport_error", f"{service} {api_name}: endpoint unreachable", {"reason": "endpoint_unreachable"}) from exc
        except bex.BotoCoreError as exc:
            raise LabError("upstream_error", f"{service} {api_name}: client error {type(exc).__name__}") from exc
        request_id = response.get("ResponseMetadata", {}).get("RequestId")
        result = redact_obj(_normalize(response))
        if request_id:
            result["_request_id"] = request_id
        return result


def _snake(api_name: str) -> str:
    out = []
    for i, ch in enumerate(api_name):
        if ch.isupper() and i and not api_name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)
