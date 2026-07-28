"""Token access control for the PTPBox web surface.

The UI and the control API share one origin, so publishing the UI publishes the
ability to stop the cascade, switch servos, release holdover and inject faults.
Sharing a link with someone outside the lab therefore needs two roles, not one:

    operator   full control, everything the local UI can do
    viewer     observation only; every mutating route is refused

Two properties matter more than the token check itself.

First, this fails closed. Historically the agent served 0.0.0.0 with no
authentication, which is defensible on a lab LAN and indefensible anywhere else.
With no tokens configured that behaviour is preserved for directly connected
private-network clients and refused for everyone else, so an appliance cannot be
exposed publicly without someone first deciding who may reach it.

Second, and less obvious: a tunnel daemon runs on the appliance itself. Cloudflare
Tunnel, ngrok and ``ssh -R`` all connect to 127.0.0.1, so a request that began on
the public internet arrives from loopback. Trusting loopback would hand the whole
control surface to the internet the moment a tunnel came up. Any request carrying
proxy or tunnel headers is therefore treated as remote regardless of its socket
address, and must present a token.

Tokens are compared with ``secrets.compare_digest`` and are never logged.
"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

VIEWER = "viewer"
OPERATOR = "operator"
ROLES = (VIEWER, OPERATOR)

# Headers that mean something forwarded this request. Their presence proves the
# socket address is not the real client, so loopback stops being evidence of
# locality.
FORWARDING_HEADERS = (
    "x-forwarded-for",
    "x-forwarded-host",
    "x-real-ip",
    "forwarded",
    "cf-connecting-ip",
    "cf-ray",
    "x-original-forwarded-for",
    "ngrok-trace-id",
    "x-tailscale-user",
)

TOKEN_HEADER = "x-ptpbox-token"
MIN_TOKEN_LENGTH = 16


@dataclass(frozen=True)
class Decision:
    """Outcome of an access check."""

    allowed: bool
    role: str | None
    reason: str
    status: int = 200

    def may_control(self) -> bool:
        return self.allowed and self.role == OPERATOR


# Spelled out rather than delegated to ``is_private``, which also covers CGNAT
# neighbours, benchmark and documentation ranges, and whose membership has shifted
# between Python releases. An implicit trust list is the wrong thing to inherit
# from a standard library, and carrier-grade NAT in particular is where a VPN such
# as Tailscale lives: reaching the appliance over a VPN should still need a token.
LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10",
    )
)


def _private(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in LOCAL_NETWORKS)


def load_tokens(path: Path | None = None, environment: dict[str, str] | None = None) -> dict[str, str]:
    """Map token to role from a file or the environment.

    File form is ``{"operator": ["..."], "viewer": ["..."]}``. The environment
    form is ``PTPBOX_OPERATOR_TOKENS`` / ``PTPBOX_VIEWER_TOKENS``, comma
    separated, which is what a systemd drop-in can set without a file.
    """
    environment = environment if environment is not None else dict(os.environ)
    tokens: dict[str, str] = {}

    def absorb(role: str, values: Iterable[Any]) -> None:
        for value in values:
            text = str(value).strip()
            # A short token is worse than none, because it looks like protection.
            if len(text) >= MIN_TOKEN_LENGTH:
                tokens[text] = role

    for role in ROLES:
        raw = environment.get(f"PTPBOX_{role.upper()}_TOKENS", "")
        absorb(role, (item for item in raw.split(",") if item.strip()))

    if path is None:
        candidate = environment.get("PTPBOX_TOKENS_FILE")
        path = Path(candidate) if candidate else None
    if path is not None and path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            for role in ROLES:
                entry = payload.get(role)
                if isinstance(entry, str):
                    absorb(role, [entry])
                elif isinstance(entry, list):
                    absorb(role, entry)
    return tokens


def presented_token(headers: Any, query: dict[str, list[str]] | None) -> str | None:
    """Pull a token from the Authorization header, a custom header, or the query.

    The query form exists so a single link can be handed to someone; the UI moves
    it into session storage and strips it from the address bar, because a token in
    a URL otherwise lands in history, logs and referrers.
    """
    if headers is not None:
        authorization = headers.get("Authorization") or ""
        if authorization.lower().startswith("bearer "):
            candidate = authorization[7:].strip()
            if candidate:
                return candidate
        custom = headers.get(TOKEN_HEADER) or headers.get(TOKEN_HEADER.title())
        if custom and custom.strip():
            return custom.strip()
    if query:
        for key in ("token", "access_token"):
            values = query.get(key)
            if values and str(values[0]).strip():
                return str(values[0]).strip()
    return None


def looks_forwarded(headers: Any) -> bool:
    if headers is None:
        return False
    for name in FORWARDING_HEADERS:
        if headers.get(name) or headers.get(name.title()):
            return True
    return False


def authorize(
    client_address: str,
    headers: Any,
    query: dict[str, list[str]] | None,
    mutating: bool,
    tokens: dict[str, str] | None = None,
) -> Decision:
    """Decide whether one request may proceed, and with which role."""
    tokens = tokens if tokens is not None else load_tokens()
    presented = presented_token(headers, query)
    forwarded = looks_forwarded(headers)

    if presented is not None:
        role = None
        # Constant-time comparison against every configured token, so a wrong
        # token cannot be discovered by timing.
        for candidate, candidate_role in tokens.items():
            if secrets.compare_digest(presented, candidate):
                role = candidate_role
        if role is None:
            return Decision(False, None, "token is not recognised", 403)
        if mutating and role != OPERATOR:
            return Decision(False, role, "this token is read-only", 403)
        return Decision(True, role, "token accepted")

    if tokens:
        # Once an operator has configured tokens, anonymous access is over, even
        # from the LAN: otherwise the tokens would only protect the tunnel while
        # the local network stayed wide open.
        return Decision(False, None, "a token is required", 401)

    if forwarded:
        return Decision(
            False, None,
            "this request was forwarded by a proxy or tunnel, which requires a token; "
            "set PTPBOX_VIEWER_TOKENS and PTPBOX_OPERATOR_TOKENS before publishing "
            "the UI", 401,
        )
    if not _private(client_address):
        return Decision(
            False, None,
            f"{client_address} is outside the local network and no tokens are "
            "configured", 401,
        )
    return Decision(True, OPERATOR, "unauthenticated local client on a private network")


def generate_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def summary(tokens: dict[str, str] | None = None) -> dict[str, Any]:
    """Non-secret description of the access posture, safe to serve."""
    tokens = tokens if tokens is not None else load_tokens()
    counts = {role: sum(1 for value in tokens.values() if value == role) for role in ROLES}
    return {
        "tokens_configured": bool(tokens),
        "operator_tokens": counts[OPERATOR],
        "viewer_tokens": counts[VIEWER],
        "anonymous_local_access": not tokens,
        "interpretation": (
            "Viewer tokens observe only; every mutating route needs an operator "
            "token. With no tokens configured the agent serves directly connected "
            "private-network clients and refuses forwarded requests, so publishing "
            "the UI through a tunnel requires tokens first."
        ),
    }
