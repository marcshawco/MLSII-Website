# MLSII Website

Personal portfolio website for **Marcus Shaw**, built with Flask and deployed on Vercel.

This repository currently includes:
- A homepage
- A portfolio page powered by BunnyCDN media
- A Link in Bio page
- A GAMELIFE Privacy Policy page for iOS App Store compliance

## Current Scope (Important)

The old **Project Tesla** integration has been fully removed from this codebase.

There are no Tesla routes, OAuth flows, Fleet API calls, or Tesla assets in the current app.

## Tech Stack

- Python 3 + Flask
- Jinja2 templates
- Vanilla CSS + JS
- BunnyCDN (for portfolio media storage/delivery)
- Vercel (serverless deployment)
- Optional Vercel Speed Insights script in production mode

## Project Structure

```text
MLSII-Website/
├── app.py                    # Main Flask app and routes
├── wsgi.py                   # WSGI entrypoint (for Gunicorn/hosting)
├── requirements.txt          # Python dependencies
├── vercel.json               # Vercel build/runtime/routes config
├── .env.example              # Example environment variables
├── templates/
│   ├── base.html             # Shared layout, nav, footer/social links
│   ├── index.html            # Homepage
│   ├── portfolio.html        # Portfolio page
│   ├── links.html            # Link in Bio page
│   └── privacy_policy.html   # GAMELIFE privacy policy page
├── static/
│   ├── css/styles.css        # Site styling
│   ├── js/menu.js            # Mobile/side menu behavior
│   ├── js/main.js            # Additional JS
│   ├── images/               # Site images
│   └── portfolio/            # Local portfolio assets (if used)
└── src/                      # Legacy/alternate frontend files (not active Flask runtime)
```

## App Behavior

### Domain-based site title
`app.py` maps the displayed title based on request host:
- `saintlazell.com` -> `SAINTLAZELL`
- `marcuslshaw.com` -> `MARCUS SHAW`
- `thesaintmarcus.com` -> `MARC SHAW`
- fallback -> `MARC SHAW`

### Security headers
Responses include default headers via `@app.after_request`:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: SAMEORIGIN`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: geolocation=(), microphone=(), camera=()`
- `Strict-Transport-Security` on secure requests

### Routes
- `/` -> homepage
- `/portfolio` -> portfolio page (BunnyCDN-driven media list)
- `/links` -> Link in Bio page
- `/privacy-policy` -> GAMELIFE privacy policy page
- `/gamelife-privacy-policy` -> same page (alias route)

## Portfolio Media Pipeline (BunnyCDN)

Portfolio media is fetched from Bunny Storage API at runtime:
1. Flask calls Bunny Storage list endpoint.
2. Filters files by extension (`.jpg`, `.jpeg`, `.png`, `.gif`, `.mp4`).
3. Builds public URLs using your Bunny Pull Zone.
4. Renders cards in `templates/portfolio.html`.

If Bunny env vars are missing, portfolio falls back gracefully to an empty list.

## Environment Variables

Copy `.env.example` to `.env` and fill values:

```bash
cp .env.example .env
```

Required for portfolio media:
- `BUNNY_PULL_ZONE_URL` (example: `https://your-zone.b-cdn.net`)
- `BUNNY_STORAGE_ZONE` (Bunny storage zone name)
- `BUNNY_ACCESS_KEY` (Bunny storage API key)

## Local Development

### 1. Create and activate virtualenv

```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run Flask app

```bash
python app.py
```

Default dev URL: `http://127.0.0.1:5000`

## Production Run (WSGI)

You can run with Gunicorn using `wsgi.py`:

```bash
gunicorn wsgi:app
```

## Deploying to Vercel

This repo is already configured for Vercel with:
- Python runtime build for `app.py`
- Static file handling for `/static/*`
- Catch-all route to Flask app

`vercel.json` controls these routes.

### Vercel environment variables
Set these in the Vercel project settings:
- `BUNNY_PULL_ZONE_URL`
- `BUNNY_STORAGE_ZONE`
- `BUNNY_ACCESS_KEY`

Then redeploy.

## Editing Content Quickly

- Update global nav/footer: `templates/base.html`
- Update homepage links: `templates/index.html`
- Update Link in Bio buttons: `templates/links.html`
- Update Privacy Policy text: `templates/privacy_policy.html`
- Update styles/spacing/responsiveness: `static/css/styles.css`

## Notes on Caching

`templates/base.html` uses a CSS query-string version value on `styles.css` to help force cache refresh after style changes. If CSS changes are not visible, bump that version string.

## Troubleshooting

### Portfolio is empty
- Verify Bunny env vars are set correctly.
- Confirm files exist in the root of the configured Bunny storage zone.
- Confirm file extensions are supported by `get_media_from_bunny()`.

### Styling changes not showing
- Hard refresh browser (`Cmd+Shift+R` / `Ctrl+F5`).
- Bump the CSS version query value in `templates/base.html`.

### Wrong site title on a domain
- Add/update domain mapping in `get_site_title()` in `app.py`.

## Maintenance Recommendation

Keep this site “set-and-forget” by limiting external API dependencies to static/media systems only (current state does this).
