import os
import requests
from flask import Flask, render_template, request
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
    return response

def get_media_from_bunny():
    """Fetch media files from BunnyCDN."""
    if not all([BUNNY_STORAGE_ZONE, BUNNY_API_KEY, BUNNY_PULL_ZONE_URL]):
        return []

    url = f"https://la.storage.bunnycdn.com/{BUNNY_STORAGE_ZONE}/"
    headers = {"AccessKey": BUNNY_API_KEY}
    
    try:
        response = requests.get(url, headers=headers)
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
        return media
    except Exception:
        return []

def get_site_title():
    domain = request.host.split(':')[0].lower()  # Convert to lowercase
    if domain.startswith('www.'):
        domain = domain[4:]  # Remove www.
    
    title_mapping = {
        'saintlazell.com': 'SAINTLAZELL',
        'marcuslshaw.com': 'MARCUS SHAW',
        'thesaintmarcus.com': 'THE SAINT MARCUS'
    }
    return title_mapping.get(domain, 'THE SAINT MARCUS')

@app.route('/')
def index():
    title = get_site_title()
    return render_template('index.html', title=title)

@app.route('/portfolio')
def portfolio():
    title = get_site_title()
    portfolio_items = get_media_from_bunny()
    return render_template('portfolio.html', portfolio_items=portfolio_items, title=title)

@app.route('/links')
def links():
    title = get_site_title()
    return render_template('links.html', title=title)

@app.route('/enemies')
def enemies():
    title = get_site_title()
    return render_template('enemies.html', title=title)

@app.route('/privacy-policy')
@app.route('/gamelife-privacy-policy')
def privacy_policy():
    title = get_site_title()
    return render_template('privacy_policy.html', title=title)

if __name__ == '__main__':
    app.run(debug=True)
