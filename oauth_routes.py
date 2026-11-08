"""Single-user OAuth 2.1 helpers for remote MCP clients."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from flask import Request

import context_store
import db

LOCKOUT_WINDOW_MINUTES = 15
LOCKOUT_MAX_FAILURES = 10


def public_base_url(request: Request) -> str:
    """Prefer configured public URL; otherwise derive from the incoming request."""
    configured = (os.environ.get('CONTEXT_PUBLIC_URL') or '').strip().rstrip('/')
    if configured:
        return configured

    # Vercel / reverse proxies
    proto = request.headers.get('X-Forwarded-Proto', request.scheme)
    host = request.headers.get('X-Forwarded-Host') or request.host
    return f'{proto}://{host}'.rstrip('/')


def password_matches(candidate: str) -> bool:
    expected = (os.environ.get('CONTEXT_PASSWORD') or '').strip()
    if not expected or candidate is None:
        return False
    return secrets.compare_digest(candidate, expected)


def resource_metadata(base_url: str) -> Dict[str, Any]:
    return {
        'resource': f'{base_url}/mcp',
        'authorization_servers': [base_url],
        'scopes_supported': ['mcp'],
        'bearer_methods_supported': ['header'],
    }


def authorization_server_metadata(base_url: str) -> Dict[str, Any]:
    return {
        'issuer': base_url,
        'authorization_endpoint': f'{base_url}/oauth/authorize',
        'token_endpoint': f'{base_url}/oauth/token',
        'registration_endpoint': f'{base_url}/oauth/register',
        'response_types_supported': ['code'],
        'grant_types_supported': ['authorization_code', 'refresh_token'],
        'code_challenge_methods_supported': ['S256'],
        'token_endpoint_auth_methods_supported': ['none', 'client_secret_post', 'client_secret_basic'],
        'scopes_supported': ['mcp'],
        'authorization_response_iss_parameter_supported': True,
    }


def www_authenticate_header(base_url: str, error: str = 'invalid_token') -> str:
    metadata_url = f'{base_url}/.well-known/oauth-protected-resource'
    header = (
        f'Bearer realm="personal-context", '
        f'resource_metadata="{metadata_url}", '
        f'scope="mcp"'
    )
    if error == 'invalid_token':
        header += ', error="invalid_token"'
    return header


def too_many_failures(ip: str) -> bool:
    row = db.fetch_one(
        """SELECT count(*) AS n FROM auth_failures
           WHERE ip = %s AND at > now() - make_interval(mins => %s)""",
        (ip, LOCKOUT_WINDOW_MINUTES),
    )
    return bool(row and row['n'] >= LOCKOUT_MAX_FAILURES)


def record_failure(ip: str) -> None:
    db.execute('INSERT INTO auth_failures (ip) VALUES (%s)', (ip,))
    db.execute("DELETE FROM auth_failures WHERE at < now() - interval '1 day'")


def extract_bearer_token(request: Request) -> Optional[str]:
    auth = request.headers.get('Authorization', '')
    if auth.lower().startswith('bearer '):
        token = auth[7:].strip()
        return token or None
    # Some clients send the token in a custom header during setup.
    alt = request.headers.get('X-Context-Token', '').strip()
    return alt or None


def require_access_token(request: Request) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return (token_payload, error_message)."""
    token = extract_bearer_token(request)
    if not token:
        return None, 'missing_token'
    payload = context_store.validate_access_token(token)
    if not payload:
        return None, 'invalid_token'
    return payload, None


def verify_pkce(code_verifier: str, code_challenge: str, method: str) -> bool:
    if not code_verifier or not code_challenge:
        return False
    method = (method or 'S256').upper()
    if method != 'S256':
        return False
    digest = hashlib.sha256(code_verifier.encode('ascii')).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')
    return hmac.compare_digest(computed, code_challenge)


def parse_basic_auth(request: Request) -> Tuple[Optional[str], Optional[str]]:
    header = request.headers.get('Authorization', '')
    if not header.lower().startswith('basic '):
        return None, None
    try:
        decoded = base64.b64decode(header[6:].strip()).decode('utf-8')
    except Exception:
        return None, None
    if ':' not in decoded:
        return None, None
    client_id, client_secret = decoded.split(':', 1)
    return client_id, client_secret


def build_redirect(redirect_uri: str, params: Dict[str, str]) -> str:
    separator = '&' if ('?' in redirect_uri) else '?'
    return f'{redirect_uri}{separator}{urlencode(params)}'


def normalize_redirect_uris(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
