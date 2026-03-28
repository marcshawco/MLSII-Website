import os
import json
import time
import requests
from flask import Flask, g, render_template, request
from urllib.parse import quote
from dotenv import load_dotenv

app = Flask(__name__, static_folder='static')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
load_dotenv(interpolate=False)

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

# Warm lambda instances can reuse this to avoid re-fetching Bunny on every request.
BUNNY_MEDIA_CACHE = {
    'expires_at': 0.0,
    'data': [],
}


@app.before_request
def track_request_start():
    g.request_started_at = time.perf_counter()

@app.after_request
def add_header(response):
    if app.debug:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '-1'

    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
    if request.is_secure:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')

    if not app.debug and response.status_code == 200 and response.mimetype == 'text/html':
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
    return render_template('links.html', title=title)

@app.route('/essentials')
def essentials():
    title = 'MARC SHAW'
    return render_template('essentials.html', title=title)

@app.route('/enemies')
def enemies():
    title = get_site_title()
    return render_template('enemies.html', title=title)

if __name__ == '__main__':
    app.run(debug=True)
