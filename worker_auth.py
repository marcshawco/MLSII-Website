"""Authorises the background worker endpoints (/api/import-step, /api/dream).

Two callers are accepted:
- Vercel Cron, which sends `Authorization: Bearer $CRON_SECRET`.
- The GitHub Actions workflow `.github/workflows/memory-worker.yml`, which sends a
  short-lived GitHub OIDC token instead of a stored secret. The token is signed by
  GitHub and only accepted when it was minted for this repository's main branch,
  for that workflow, on a schedule or manual run, with our audience.
"""

from __future__ import annotations

import hmac
import os

import jwt

GITHUB_ISSUER = 'https://token.actions.githubusercontent.com'
GITHUB_JWKS_URL = f'{GITHUB_ISSUER}/.well-known/jwks'
WORKER_AUDIENCE = 'marcshaw-memory-worker'
ALLOWED_EVENTS = {'schedule', 'workflow_dispatch'}

_jwks_client = None


def _setting(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip()


def _expected_claims() -> dict:
    repo = _setting('WORKER_GITHUB_REPOSITORY', 'marcshawco/MLSII-Website')
    return {
        'repository': repo,
        'repository_id': _setting('WORKER_GITHUB_REPOSITORY_ID', '894205893'),
        'repository_owner_id': _setting('WORKER_GITHUB_OWNER_ID', '189466376'),
        'ref': 'refs/heads/main',
        'workflow_ref': f'{repo}/.github/workflows/memory-worker.yml@refs/heads/main',
    }


def _signing_key(token: str):
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(GITHUB_JWKS_URL, cache_keys=True, lifespan=3600, timeout=10)
    return _jwks_client.get_signing_key_from_jwt(token).key


def verify_github_oidc(token: str, key=None) -> bool:
    """True only for a valid GitHub OIDC token from our worker workflow on main."""
    try:
        claims = jwt.decode(
            token,
            key if key is not None else _signing_key(token),
            algorithms=['RS256'],
            audience=WORKER_AUDIENCE,
            issuer=GITHUB_ISSUER,
            options={'require': ['exp', 'iat', 'iss', 'aud', 'sub']},
            leeway=30,
        )
    except Exception:
        return False
    for name, expected in _expected_claims().items():
        if str(claims.get(name, '')) != expected:
            return False
    return claims.get('event_name') in ALLOWED_EVENTS


def authorized(auth_header: str, key=None) -> str | None:
    """Returns the caller kind ('cron' or 'github') when allowed, else None."""
    if not auth_header.startswith('Bearer '):
        return None
    secret = (os.environ.get('CRON_SECRET') or '').strip()
    if secret and hmac.compare_digest(auth_header, f'Bearer {secret}'):
        return 'cron'
    token = auth_header[len('Bearer '):].strip()
    if token.count('.') == 2 and verify_github_oidc(token, key=key):
        return 'github'
    return None
