"""Public-safe identity evidence for bounded LLM providers.

The helpers in this module do not open sockets or resolve DNS.  They only parse
operator/injected endpoint values and decide whether a local-only role has
enough immutable boundary evidence to proceed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class EndpointIdentity:
    scheme: str
    host: str
    port: int | None
    base_path: str
    normalized_base_url: str
    loopback_proven: bool
    problems: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def endpoint_identity_from_url(base_url: str) -> EndpointIdentity:
    raw = str(base_url or "").strip()
    if not raw:
        return EndpointIdentity("", "", None, "", "", False, ("missing_endpoint",))

    parsed = urlparse(raw)
    problems: list[str] = []
    scheme = parsed.scheme.lower()
    if scheme != "http":
        problems.append("non_http_endpoint")
    if parsed.username or parsed.password:
        problems.append("endpoint_userinfo_forbidden")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        problems.append("missing_endpoint_host")

    normalized_host = host
    if host == "localhost":
        normalized_host = "127.0.0.1"
    loopback = normalized_host in {"127.0.0.1", "::1"}
    if host and not loopback:
        problems.append("non_loopback_endpoint")

    port = parsed.port
    path = parsed.path.rstrip("/") or ""
    if normalized_host == "::1":
        host_part = "[::1]"
    else:
        host_part = normalized_host
    port_part = f":{port}" if port is not None else ""
    normalized = f"{scheme}://{host_part}{port_part}{path}" if scheme and host_part else ""
    return EndpointIdentity(
        scheme=scheme,
        host=normalized_host,
        port=port,
        base_path=path,
        normalized_base_url=normalized,
        loopback_proven=bool(loopback and not problems),
        problems=tuple(problems),
    )
