"""OAuth persistence for the shared memory MCP (Postgres-backed).

Codes and tokens are stored as sha256 hashes. Every token is bound to the peer
(claude, gemini, grok, muse) chosen on the consent screen, which is how writes
get attributed without trusting the calling AI.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import db
import memory

AUTH_CODE_TTL_SECONDS = 300
ACCESS_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30   # 30 days
REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 180  # 180 days, rotated on use


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def context_configured() -> bool:
    return bool((os.environ.get('CONTEXT_PASSWORD') or '').strip()) and db.configured()


def missing_config() -> List[str]:
    missing = []
    if not (os.environ.get('CONTEXT_PASSWORD') or '').strip():
        missing.append('CONTEXT_PASSWORD')
    if not db.configured():
        missing.append('DATABASE_URL')
    return missing


def guess_peer(client_name: str) -> str:
    name = (client_name or '').lower()
    for peer in memory.ASSISTANT_PEERS:
        if peer in name:
            return peer
    if 'anthropic' in name:
        return 'claude'
    if 'google' in name:
        return 'gemini'
    if 'xai' in name or 'x.ai' in name:
        return 'grok'
    if 'meta' in name:  # Meta's Muse agent (muse.ai)
        return 'muse'
    return ''


# --------------------------------------------------------------------------- clients

def register_oauth_client(
    redirect_uris: List[str],
    client_name: str = '',
    token_endpoint_auth_method: str = 'none',
) -> Dict[str, Any]:
    if not redirect_uris or not isinstance(redirect_uris, list):
        raise ValueError('redirect_uris is required')
    cleaned = []
    for uri in redirect_uris:
        if not isinstance(uri, str) or not uri.strip():
            continue
        uri = uri.strip()
        parsed = urlparse(uri)
        if not parsed.scheme or parsed.scheme.lower() in ('javascript', 'data', 'file', 'vbscript'):
            continue
        # Plain http only for loopback (desktop clients); everything else must be https or an app scheme.
        if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            continue
        cleaned.append(uri)
    if not cleaned:
        raise ValueError('At least one valid redirect_uri is required')
    if len(cleaned) > 10:
        raise ValueError('Too many redirect_uris')

    client = {
        'client_id': secrets.token_urlsafe(24),
        'client_name': (client_name or 'MCP client')[:120],
        'redirect_uris': cleaned,
        'token_endpoint_auth_method': token_endpoint_auth_method or 'none',
    }
    db.execute(
        """INSERT INTO oauth_clients (client_id, client_name, redirect_uris, token_endpoint_auth_method)
           VALUES (%s, %s, %s::jsonb, %s)""",
        (client['client_id'], client['client_name'], json.dumps(cleaned), client['token_endpoint_auth_method']),
    )
    return client


def get_oauth_client(client_id: str) -> Optional[Dict[str, Any]]:
    if not client_id:
        return None
    return db.fetch_one(
        'SELECT client_id, client_name, redirect_uris, token_endpoint_auth_method FROM oauth_clients WHERE client_id = %s',
        (client_id,),
    )


# --------------------------------------------------------------------------- codes

def create_auth_code(
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    peer_id: str,
    scope: str = 'mcp',
) -> str:
    if peer_id not in memory.ASSISTANT_PEERS:
        raise ValueError('Choose which assistant this connection is for.')
    code = secrets.token_urlsafe(32)
    db.execute(
        """INSERT INTO oauth_codes
             (code_hash, client_id, redirect_uri, code_challenge, code_challenge_method, scope, peer_id, expires_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, now() + make_interval(secs => %s))""",
        (_hash(code), client_id, redirect_uri, code_challenge, code_challenge_method, scope, peer_id,
         AUTH_CODE_TTL_SECONDS),
    )
    return code


def consume_auth_code(code: str) -> Optional[Dict[str, Any]]:
    if not code:
        return None
    return db.fetch_one(
        """DELETE FROM oauth_codes WHERE code_hash = %s AND expires_at > now()
           RETURNING client_id, redirect_uri, code_challenge, code_challenge_method, scope, peer_id""",
        (_hash(code),),
    )


# --------------------------------------------------------------------------- tokens

def _token_response(access: str, refresh: str, scope: str) -> Dict[str, Any]:
    return {
        'access_token': access,
        'token_type': 'Bearer',
        'expires_in': ACCESS_TOKEN_TTL_SECONDS,
        'refresh_token': refresh,
        'scope': scope,
    }


def issue_access_token(client_id: str, peer_id: str, scope: str = 'mcp') -> Dict[str, Any]:
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.execute(
        """INSERT INTO oauth_tokens
             (access_hash, refresh_hash, client_id, peer_id, scope, access_expires_at, refresh_expires_at)
           VALUES (%s, %s, %s, %s, %s, now() + make_interval(secs => %s), now() + make_interval(secs => %s))""",
        (_hash(access), _hash(refresh), client_id, peer_id, scope,
         ACCESS_TOKEN_TTL_SECONDS, REFRESH_TOKEN_TTL_SECONDS),
    )
    return _token_response(access, refresh, scope)


def refresh_access_token(refresh_token: str, client_id: str = '') -> Optional[Dict[str, Any]]:
    """Rotate: the old token pair is revoked and a new pair is issued."""
    if not refresh_token:
        return None
    with db.transaction() as conn:
        row = db.fetch_one(
            """UPDATE oauth_tokens SET revoked_at = now()
               WHERE refresh_hash = %s AND revoked_at IS NULL AND refresh_expires_at > now()
                 AND (%s = '' OR client_id = %s)
               RETURNING client_id, peer_id, scope""",
            (_hash(refresh_token), client_id, client_id),
            conn=conn,
        )
        if not row:
            return None
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        conn.execute(
            """INSERT INTO oauth_tokens
                 (access_hash, refresh_hash, client_id, peer_id, scope, access_expires_at, refresh_expires_at)
               VALUES (%s, %s, %s, %s, %s, now() + make_interval(secs => %s), now() + make_interval(secs => %s))""",
            (_hash(access), _hash(refresh), row['client_id'], row['peer_id'], row['scope'],
             ACCESS_TOKEN_TTL_SECONDS, REFRESH_TOKEN_TTL_SECONDS),
        )
    return _token_response(access, refresh, row['scope'])


def validate_access_token(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    return db.fetch_one(
        """UPDATE oauth_tokens SET last_used_at = now()
           WHERE access_hash = %s AND revoked_at IS NULL AND access_expires_at > now()
           RETURNING client_id, peer_id, scope""",
        (_hash(token),),
    )


def revoke_peer_tokens(peer_id: str) -> int:
    return db.execute(
        'UPDATE oauth_tokens SET revoked_at = now() WHERE peer_id = %s AND revoked_at IS NULL', (peer_id,)
    )


def list_connections() -> List[Dict[str, Any]]:
    return db.fetch_all(
        """SELECT t.peer_id, c.client_name, count(*) AS tokens, max(t.last_used_at) AS last_used_at,
                  max(t.created_at) AS connected_at
           FROM oauth_tokens t JOIN oauth_clients c USING (client_id)
           WHERE t.revoked_at IS NULL AND COALESCE(t.refresh_expires_at, t.access_expires_at) > now()
           GROUP BY t.peer_id, c.client_name
           ORDER BY t.peer_id, c.client_name"""
    )
