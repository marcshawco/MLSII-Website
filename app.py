import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import timedelta
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)

import context_store
import db
import mcp_handler
import memory
import oauth_routes
import reasoning

app = Flask(__name__, static_folder='static')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
load_dotenv(interpolate=False)
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env.local'), interpolate=False, override=True)  # written by `vercel env pull`

# Session secret for the owner /context UI. Prefer an explicit env var.
_session_secret = (
    os.environ.get('CONTEXT_SESSION_SECRET')
    or os.environ.get('CONTEXT_PASSWORD')
    or 'dev-only-context-session'
)
app.secret_key = hashlib.sha256(_session_secret.encode('utf-8')).hexdigest()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=bool(os.environ.get('VERCEL')),
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
)

if app.debug:
    app.config['TEMPLATES_AUTO_RELOAD'] = True

BUNNY_STORAGE_ZONE = os.environ.get('BUNNY_STORAGE_ZONE')
BUNNY_API_KEY = os.environ.get('BUNNY_ACCESS_KEY')
BUNNY_PULL_ZONE_URL = os.environ.get('BUNNY_PULL_ZONE_URL')
BUNNY_MEDIA_CACHE_TTL_SECONDS = int(os.environ.get('BUNNY_MEDIA_CACHE_TTL_SECONDS', '300'))
HTML_CACHE_TTL_SECONDS = int(os.environ.get('HTML_CACHE_TTL_SECONDS', '300'))
PORTFOLIO_HTML_CACHE_TTL_SECONDS = int(os.environ.get('PORTFOLIO_HTML_CACHE_TTL_SECONDS', '60'))
PORTFOLIO_MEDIA_SNAPSHOT_PATH = os.path.join(
    app.root_path, 'static', 'data', 'portfolio-media.json'
)
PRIVATE_LINKS_PATH = os.path.join(app.root_path, 'private', 'links.json')
NO_STORE_ENDPOINTS = frozenset({
    'mcp_endpoint',
    'oauth_protected_resource',
    'oauth_authorization_server',
    'oauth_register',
    'oauth_authorize',
    'oauth_token',
    'context_login',
    'context_admin',
    'context_logout',
    'dream_endpoint',
})

# Warm lambda instances can reuse this to avoid re-fetching Bunny on every request.
BUNNY_MEDIA_CACHE = {
    'expires_at': 0.0,
    'data': [],
}


@app.before_request
def track_request_start():
    g.request_started_at = time.perf_counter()
    oidc = request.headers.get('x-vercel-oidc-token')
    if oidc:
        reasoning.set_request_oidc_token(oidc)


@app.after_request
def add_header(response):
    if app.debug or request.endpoint in NO_STORE_ENDPOINTS:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '-1'

    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
    if request.is_secure:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')

    if (
        not app.debug
        and request.endpoint not in NO_STORE_ENDPOINTS
        and response.status_code == 200
        and response.mimetype == 'text/html'
    ):
        html_ttl = HTML_CACHE_TTL_SECONDS
        if request.endpoint == 'portfolio':
            html_ttl = PORTFOLIO_HTML_CACHE_TTL_SECONDS
        response.headers['Cache-Control'] = (
            f'public, max-age=0, s-maxage={html_ttl}, stale-while-revalidate={html_ttl * 2}'
        )

    duration_ms = None
    if hasattr(g, 'request_started_at'):
        duration_ms = (time.perf_counter() - g.request_started_at) * 1000
    app.logger.info(
        'request method=%s path=%s endpoint=%s status=%s duration_ms=%.2f',
        request.method,
        request.path,
        request.endpoint or 'unknown',
        response.status_code,
        duration_ms or 0.0,
    )
    return response


def load_media_snapshot():
    try:
        with open(PORTFOLIO_MEDIA_SNAPSHOT_PATH, 'r', encoding='utf-8') as snapshot_file:
            snapshot = json.load(snapshot_file)
        if isinstance(snapshot, list):
            return snapshot
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    return []


SNAPSHOT_MEDIA = load_media_snapshot()
if SNAPSHOT_MEDIA:
    BUNNY_MEDIA_CACHE['data'] = SNAPSHOT_MEDIA
    BUNNY_MEDIA_CACHE['expires_at'] = time.time() + BUNNY_MEDIA_CACHE_TTL_SECONDS


def load_private_links():
    links = {}

    try:
        with open(PRIVATE_LINKS_PATH, 'r', encoding='utf-8') as links_file:
            private_links = json.load(links_file)
        if isinstance(private_links, dict):
            links = private_links
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        links = {}

    m1_referral_url = os.environ.get('M1_REFERRAL_URL') or links.get('m1_referral_url', '')
    if not isinstance(m1_referral_url, str):
        m1_referral_url = ''

    return {
        'm1_referral_url': m1_referral_url.strip(),
    }


def get_media_from_bunny():
    """Fetch media files from BunnyCDN."""
    if not all([BUNNY_STORAGE_ZONE, BUNNY_API_KEY, BUNNY_PULL_ZONE_URL]):
        return SNAPSHOT_MEDIA

    now = time.time()
    if BUNNY_MEDIA_CACHE['expires_at'] > now:
        return BUNNY_MEDIA_CACHE['data']

    url = f"https://la.storage.bunnycdn.com/{BUNNY_STORAGE_ZONE}/"
    headers = {"AccessKey": BUNNY_API_KEY}

    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        files = response.json()

        media = []
        for file in files:
            if not file.get('IsDirectory'):
                file_ext = file['ObjectName'].lower()
                if any(file_ext.endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.gif', '.mp4')):
                    media.append({
                        "url": f"{BUNNY_PULL_ZONE_URL}/{quote(file['ObjectName'])}",
                        "description": file['ObjectName'].split('.')[0],
                        "type": "video" if file_ext.endswith('.mp4') else "image"
                    })
        BUNNY_MEDIA_CACHE['data'] = media
        BUNNY_MEDIA_CACHE['expires_at'] = now + BUNNY_MEDIA_CACHE_TTL_SECONDS
        return media
    except Exception:
        if BUNNY_MEDIA_CACHE['data']:
            return BUNNY_MEDIA_CACHE['data']
        return SNAPSHOT_MEDIA


def get_site_title():
    domain = request.host.split(':')[0].lower()  # Convert to lowercase
    if domain.startswith('www.'):
        domain = domain[4:]  # Remove www.

    title_mapping = {
        'saintlazell.com': 'SAINTLAZELL',
        'marcuslshaw.com': 'MARCUS SHAW',
        'thesaintmarcus.com': 'MARC SHAW'
    }
    return title_mapping.get(domain, 'MARC SHAW')


def _json_no_store(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response


def _mcp_unauthorized(error='invalid_token'):
    base_url = oauth_routes.public_base_url(request)
    response = _json_no_store({'error': 'unauthorized'}, status=401)
    response.headers['WWW-Authenticate'] = oauth_routes.www_authenticate_header(base_url, error=error)
    return response


def _context_unlocked():
    return bool(session.get('context_unlocked'))


def _require_context_config():
    missing = context_store.missing_config()
    if missing:
        return _json_no_store(
            {'error': f'Shared memory is not configured on this deployment (missing: {", ".join(missing)}).'},
            status=503,
        )
    return None


def _client_ip():
    forwarded = request.headers.get('X-Forwarded-For', '')
    return (forwarded.split(',')[0].strip() or request.remote_addr or 'unknown')[:64]


def _password_ok(password):
    """Check the owner password with a per-IP lockout (10 failures / 15 min)."""
    ip = _client_ip()
    if oauth_routes.too_many_failures(ip):
        return False, 'Too many attempts. Try again in 15 minutes.'
    if oauth_routes.password_matches(password):
        return True, None
    oauth_routes.record_failure(ip)
    return False, 'Incorrect password.'


@app.route('/')
def index():
    title = get_site_title()
    return render_template('index.html', title=title)


@app.route('/portfolio')
def portfolio():
    title = 'MARC SHAW'
    portfolio_items = get_media_from_bunny()
    return render_template('portfolio.html', portfolio_items=portfolio_items, title=title)


@app.route('/links')
def links():
    title = get_site_title()
    return render_template('links.html', title=title, links=load_private_links())


@app.route('/audio')
def audio():
    title = 'MARC SHAW'
    return render_template('essentials.html', title=title)


@app.route('/enemies')
def enemies():
    title = get_site_title()
    return render_template('enemies.html', title=title)


@app.route('/.well-known/oauth-protected-resource', methods=['GET'])
@app.route('/.well-known/oauth-protected-resource/mcp', methods=['GET'])
def oauth_protected_resource():
    missing = _require_context_config()
    if missing:
        return missing
    base_url = oauth_routes.public_base_url(request)
    return _json_no_store(oauth_routes.resource_metadata(base_url))


@app.route('/.well-known/oauth-authorization-server', methods=['GET'])
@app.route('/.well-known/openid-configuration', methods=['GET'])
def oauth_authorization_server():
    missing = _require_context_config()
    if missing:
        return missing
    base_url = oauth_routes.public_base_url(request)
    return _json_no_store(oauth_routes.authorization_server_metadata(base_url))


@app.route('/oauth/register', methods=['POST'])
def oauth_register():
    missing = _require_context_config()
    if missing:
        return missing

    payload = request.get_json(silent=True) or {}
    redirect_uris = oauth_routes.normalize_redirect_uris(payload.get('redirect_uris'))
    try:
        client = context_store.register_oauth_client(
            redirect_uris=redirect_uris,
            client_name=str(payload.get('client_name') or 'MCP client'),
            token_endpoint_auth_method=str(payload.get('token_endpoint_auth_method') or 'none'),
        )
    except ValueError as exc:
        return _json_no_store({'error': 'invalid_client_metadata', 'error_description': str(exc)}, 400)

    return _json_no_store({
        'client_id': client['client_id'],
        'client_name': client['client_name'],
        'redirect_uris': client['redirect_uris'],
        'token_endpoint_auth_method': client['token_endpoint_auth_method'],
        'grant_types': ['authorization_code', 'refresh_token'],
        'response_types': ['code'],
    }, status=201)


@app.route('/oauth/authorize', methods=['GET', 'POST'])
def oauth_authorize():
    missing = _require_context_config()
    if missing:
        return missing

    values = request.values
    client_id = (values.get('client_id') or '').strip()
    redirect_uri = (values.get('redirect_uri') or '').strip()
    state = values.get('state') or ''
    scope = (values.get('scope') or 'mcp').strip() or 'mcp'
    code_challenge = (values.get('code_challenge') or '').strip()
    code_challenge_method = (values.get('code_challenge_method') or 'S256').strip() or 'S256'
    response_type = (values.get('response_type') or 'code').strip()

    client = context_store.get_oauth_client(client_id)
    client_name = (client or {}).get('client_name', 'Unknown client')
    selected_peer = (values.get('peer') or '').strip() or context_store.guess_peer(client_name)
    error = None

    if response_type != 'code':
        error = 'Only response_type=code is supported.'
    elif not client:
        error = 'Unknown client_id. Register the client first.'
    elif redirect_uri not in (client.get('redirect_uris') or []):
        error = 'redirect_uri is not registered for this client.'
    elif not code_challenge:
        error = 'PKCE code_challenge is required.'
    elif code_challenge_method.upper() != 'S256':
        error = 'Only S256 PKCE is supported.'

    template_args = dict(
        client_name=client_name,
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        scope=scope,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        peers=memory.ASSISTANT_PEERS,
        selected_peer=selected_peer,
    )

    # A bad client or redirect gets an error page, never a redirect.
    if error and (not client or redirect_uri not in (client.get('redirect_uris') or [])):
        return render_template('oauth_authorize.html', error=error, **template_args), 400

    if request.method == 'POST' and not error:
        ok, error = _password_ok(request.form.get('password') or '')
        if ok and selected_peer not in memory.ASSISTANT_PEERS:
            ok, error = False, 'Choose which assistant this connection is for.'
        if ok:
            code = context_store.create_auth_code(
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method.upper(),
                peer_id=selected_peer,
                scope=scope,
            )
            params = {'code': code}
            if state:
                params['state'] = state
            params['iss'] = oauth_routes.public_base_url(request)
            return redirect(oauth_routes.build_redirect(redirect_uri, params), code=302)

    return render_template('oauth_authorize.html', error=error, **template_args), (400 if error else 200)


def _oauth_error(error, description, status=400):
    return _json_no_store({'error': error, 'error_description': description}, status)


@app.route('/oauth/token', methods=['POST'])
def oauth_token():
    missing = _require_context_config()
    if missing:
        return missing

    payload = request.get_json(silent=True) or {}
    data = {**payload, **(request.form.to_dict() if request.form else {})}

    client_id = (data.get('client_id') or '').strip()
    basic_id, _basic_secret = oauth_routes.parse_basic_auth(request)
    if basic_id:
        client_id = basic_id

    grant_type = (data.get('grant_type') or '').strip()

    if grant_type == 'refresh_token':
        token = context_store.refresh_access_token((data.get('refresh_token') or '').strip(), client_id=client_id)
        if not token:
            return _oauth_error('invalid_grant', 'Refresh token is invalid, expired or already used.')
        return _json_no_store(token)

    if grant_type != 'authorization_code':
        return _oauth_error('unsupported_grant_type', 'Use authorization_code or refresh_token.')

    auth_code = context_store.consume_auth_code((data.get('code') or '').strip())
    if not auth_code:
        return _oauth_error('invalid_grant', 'Authorization code is invalid or expired.')
    if client_id and auth_code.get('client_id') != client_id:
        return _oauth_error('invalid_grant', 'client_id does not match the authorization code.')
    if auth_code.get('redirect_uri') != (data.get('redirect_uri') or '').strip():
        return _oauth_error('invalid_grant', 'redirect_uri does not match.')
    if not oauth_routes.verify_pkce(
        (data.get('code_verifier') or '').strip(),
        auth_code.get('code_challenge', ''),
        auth_code.get('code_challenge_method', 'S256'),
    ):
        return _oauth_error('invalid_grant', 'PKCE verification failed.')

    token = context_store.issue_access_token(
        client_id=auth_code['client_id'],
        peer_id=auth_code['peer_id'],
        scope=auth_code.get('scope') or 'mcp',
    )
    return _json_no_store(token)


def _cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = (
        'Authorization, Content-Type, Accept, Mcp-Protocol-Version, Mcp-Session-Id'
    )
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
    response.headers['Access-Control-Expose-Headers'] = 'WWW-Authenticate, Mcp-Session-Id'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/mcp', methods=['GET', 'POST', 'DELETE', 'OPTIONS'])
def mcp_endpoint():
    if request.method == 'OPTIONS':
        return _cors(Response(status=204))

    missing = _require_context_config()
    if missing:
        return _cors(missing)

    if request.method == 'DELETE':
        # Stateless transport: nothing to tear down.
        return _cors(Response(status=204))

    token_payload, token_error = oauth_routes.require_access_token(request)
    if token_error:
        return _cors(_mcp_unauthorized(error=token_error))

    if request.method == 'GET':
        # No server-initiated SSE stream; spec-compliant clients expect 405 here.
        if 'text/event-stream' in (request.headers.get('Accept') or ''):
            response = Response(status=405)
            response.headers['Allow'] = 'POST, DELETE, OPTIONS'
            return _cors(response)
        return _cors(_json_no_store({
            'ok': True,
            'server': mcp_handler.SERVER_NAME,
            'version': mcp_handler.SERVER_VERSION,
            'peer': token_payload['peer_id'],
        }))

    if request.content_length and request.content_length > 2_000_000:
        return _cors(_json_no_store({'error': 'payload_too_large'}, 413))

    payload = request.get_json(silent=True)
    if payload is None and request.data:
        try:
            payload = json.loads(request.data.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _cors(_json_no_store({'error': 'invalid_json'}, 400))

    body, status = mcp_handler.handle_payload(payload, peer_id=token_payload['peer_id'])
    if body is None:
        return _cors(Response(status=202))
    response = jsonify(body)
    response.status_code = status
    return _cors(response)


@app.route('/api/dream', methods=['GET', 'POST'])
def dream_endpoint():
    """Nightly consolidation, triggered by Vercel Cron (sends Authorization: Bearer $CRON_SECRET)."""
    secret = (os.environ.get('CRON_SECRET') or '').strip()
    auth = request.headers.get('Authorization', '')
    if not secret or not hmac.compare_digest(auth, f'Bearer {secret}'):
        return _json_no_store({'error': 'unauthorized'}, 401)
    if not db.configured():
        return _json_no_store({'error': 'DATABASE_URL is not configured.'}, 503)
    return _json_no_store(memory.run_dream())


@app.route('/context', methods=['GET', 'POST'])
def context_login():
    missing = _require_context_config()
    if missing:
        return missing

    if _context_unlocked() and request.method == 'GET':
        return redirect('/context/admin')

    error = None
    if request.method == 'POST':
        ok, error = _password_ok(request.form.get('password') or '')
        if ok:
            session.clear()
            session['context_unlocked'] = True
            session['csrf'] = secrets.token_urlsafe(24)
            session.permanent = True
            return redirect('/context/admin')

    return render_template('context_login.html', error=error)


def _admin_action(form):
    action = (form.get('action') or '').strip()
    memory_id = (form.get('memory_id') or '').strip()
    if action == 'save_card':
        items = [line.strip(' -•\t') for line in (form.get('card') or '').splitlines()]
        memory.set_card([i for i in items if i], updated_by='marcus')
        return 'Peer card saved.'
    if action == 'add_memory':
        result = memory.remember([form.get('text') or ''], observer='marcus')
        return f'Memory {result["stored"][0]["result"]}.'
    if action == 'approve':
        return 'Approved.' if memory.set_status(memory_id, 'active') else 'Memory not found.'
    if action == 'retract':
        return 'Retracted.' if memory.set_status(memory_id, 'retracted', 'Retracted by Marcus') else 'Memory not found.'
    if action == 'delete_memory':
        return 'Deleted permanently.' if memory.delete_conclusion(memory_id) else 'Memory not found.'
    if action == 'revoke_peer':
        peer = (form.get('peer') or '').strip()
        if peer not in memory.ASSISTANT_PEERS:
            return 'Unknown assistant.'
        return f'Revoked {context_store.revoke_peer_tokens(peer)} token(s) for {peer}.'
    if action == 'run_dream':
        result = memory.run_dream()
        return f'Dream finished: {json.dumps(result["stats"])}'
    return 'Unknown action.'


@app.route('/context/admin', methods=['GET', 'POST'])
def context_admin():
    missing = _require_context_config()
    if missing:
        return missing
    if not _context_unlocked():
        return redirect('/context')

    flash = None
    if request.method == 'POST':
        token = request.form.get('csrf') or ''
        if not session.get('csrf') or not hmac.compare_digest(token, session['csrf']):
            flash = 'Session expired; reload and try again.'
        else:
            try:
                flash = _admin_action(request.form)
            except ValueError as exc:
                flash = str(exc)

    status = (request.args.get('status') or 'active').strip()
    if status not in memory.STATUSES:
        status = 'active'
    kind = (request.args.get('kind') or '').strip() or None
    observer = (request.args.get('observer') or '').strip() or None
    return render_template(
        'context_admin.html',
        flash=flash,
        csrf=session.get('csrf', ''),
        card=memory.get_card(),
        pending=memory.list_conclusions(status='pending_review', limit=100),
        conclusions=memory.list_conclusions(status=status, kind=kind, observer=observer),
        filters={'status': status, 'kind': kind or '', 'observer': observer or ''},
        statuses=memory.STATUSES,
        kinds=memory.KINDS,
        observers=list(memory.WRITER_PEERS) + ['dreamer'],
        connections=context_store.list_connections(),
        assistants=memory.ASSISTANT_PEERS,
        sessions=memory.recent_sessions(10),
        dream=memory.latest_dream(),
    )


@app.route('/context/logout', methods=['GET', 'POST'])
def context_logout():
    session.clear()
    return redirect('/context')


if __name__ == '__main__':
    app.run(debug=True)
