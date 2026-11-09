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

Optional personal link:
- `M1_REFERRAL_URL` (recommended for production instead of hardcoding a personal referral URL)

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
- `M1_REFERRAL_URL` (if you want the M1 button live in production)

Then redeploy.

## Editing Content Quickly

- Update global nav/footer: `templates/base.html`
- Update homepage links: `templates/index.html`
- Update Link in Bio buttons: `templates/links.html`
- Update Privacy Policy text: `templates/privacy_policy.html`
- Update styles/spacing/responsiveness: `static/css/styles.css`

## Shared AI Memory (MCP)

This app hosts a private remote MCP server so Claude, ChatGPT, Gemini, Grok, Muse (and any assistant you add) share one long-term memory about you. The design borrows from [Honcho](https://github.com/plastic-labs/honcho):

- **Peers.** `marcus` plus one per assistant. Every write is attributed to the assistant chosen on the OAuth consent screen, never to what the AI claims.
- **Conclusions.** Typed memories: `explicit` (stated), `deductive` (inferred from premises) and `inductive` (patterns found by the dream). Near-duplicates reinforce an existing memory instead of piling up.
- **Peer card.** A short identity summary every assistant sees first. You can edit it at `/context/admin`.
- **Deriver.** `log_session` extracts facts from a conversation as soon as it is saved.
- **Dream.** A nightly Vercel Cron (`/api/dream`) merges duplicates, resolves contradictions, records patterns and rewrites the peer card.
- **Hybrid search.** pgvector similarity plus Postgres full text, fused by reciprocal rank.
- **History import.** Upload chat exports at `/context/admin`; facts are extracted in the background, oldest first, dated, and the raw chat text is deleted once processed.

Nothing from this store is rendered on the public site.

### MCP tools
| Tool | Purpose |
|---|---|
| `get_context(query?, token_budget?)` | Peer card, plus relevant and recent memories and recent sessions. Call it first. |
| `search_memory(query, kind?, observer?, limit?)` | Hybrid search |
| `remember(facts[])` | Store short third-person facts |
| `log_session(title?, summary?, messages?)` | Save a conversation; facts are extracted automatically |
| `correct_memory(id, new_content?, reason?)` | Replace or retract a memory (history is kept) |
| `ask_memory(question)` | Answer a question from memory, with cited ids |
| `get_peer_card()` | Compact identity summary |

Safety and privacy:
- AI steps go through Vercel AI Gateway with `disallowPromptTraining` on every request (`MEMORY_PRIVACY=no_training`). With paid AI Gateway credits, set `MEMORY_PRIVACY=zdr` to also require zero data retention. Requests fail rather than downgrade.
- Imported chats are kept only until their facts are extracted, then the text is wiped.
- `/context/admin` can download everything as JSON and erase everything.
- Secrets (passwords, keys, card or ID numbers) are refused.
- Text that looks like instructions to an AI goes to a review queue.
- Every read is wrapped in a "data, not instructions" note.

### Files
- `schema.sql`, `scripts/migrate.py`: Postgres schema (idempotent)
- `db.py`: connection pool
- `memory.py`: domain logic, search, dream
- `reasoning.py`: AI Gateway calls (embeddings, deriver, dream, answers), with offline stubs
- `context_store.py`, `oauth_routes.py`: OAuth 2.1 + PKCE + dynamic registration + refresh rotation (hashed tokens)
- `mcp_handler.py`: Streamable HTTP JSON-RPC

### Setup
1. Add **Neon** to the Vercel project from the Marketplace. It injects `DATABASE_URL`. Use a dedicated Neon resource; don't share one with another app.
2. Set `CONTEXT_PASSWORD`, `CONTEXT_PUBLIC_URL` (e.g. `https://www.marcshaw.co`) and `CRON_SECRET` in Vercel.
3. Make sure AI Gateway is enabled for the team. Deployed functions authenticate with Vercel OIDC automatically. For local runs, set `AI_GATEWAY_API_KEY` or `REASONING_FAKE=1`.
4. Run `vercel env pull`, then `python scripts/migrate.py`.
5. Deploy, open `/context`, and write your peer card.
6. Nothing to set up for the frequent worker. Vercel Hobby crons run once a day, so `.github/workflows/memory-worker.yml` nudges the import every 30 minutes and the dream every 6 hours. It signs in with a short-lived GitHub OIDC token instead of a stored secret, and `worker_auth.py` only accepts tokens minted for that workflow on `main` of this repository.

### Importing your history
1. Export your chats: ChatGPT (Settings → Data controls → Export data), Claude (Settings → Privacy → Export data), Grok (Settings → Data → Download), Gemini (Google Takeout → My Activity → Gemini Apps, JSON format). Other apps: any `.json`, `.txt` or `.md` file.
2. At `/context/admin` → Import history, pick the `.zip` or `conversations.json`, check the detected source, and Upload. You can close the page afterwards.
3. Processing runs in the background. **Process now** speeds it up while the page is open. On the AI Gateway free tier ($5 of credits per 30 days, rate limited) a large history can take days or weeks. When credits or rate limits run out, the import pauses and resumes automatically.
4. Re-uploading the same export skips conversations already imported.

### Connect assistants
Use the connector URL `https://<your-domain>/mcp` (Streamable HTTP, OAuth). On the consent page, pick which assistant it is and enter `CONTEXT_PASSWORD`. For an app that isn't listed (ChatGPT is included), add it first under Connected assistants at `/context/admin`.
- **Claude:** Settings → Connectors → Add custom connector.
- **Muse (Meta, muse.ai):** Settings → Connectors → Add custom connector, then paste the URL. If that button is missing in your app version, ask Muse in chat to create a custom connector named "Shared Memory" with transport remote streamable HTTP, auth OAuth, and the URL above. Muse connects from Meta's cloud, so use the public HTTPS domain (localhost won't work). Access tokens last 30 days, so Muse's reported lack of refresh-token support only means signing in again about once a month.
- **ChatGPT:** Settings → Apps & Connectors → developer mode → add a custom connector with the same URL, where your plan supports it.
- **Gemini / Grok:** add a custom MCP connector or remote MCP server with the same URL, if the app supports it.

In each assistant's custom instructions, add: "Use my memory connector. Call get_context at the start of personal conversations, remember when I share something durable, and log_session at the end of long chats."

### Local verification
Run the checks against a throwaway Postgres with pgvector. **The script wipes it.**

```bash
docker run -d --name mlsii-memory-pg -e POSTGRES_PASSWORD=devpass -e POSTGRES_DB=memory -p 55432:5432 pgvector/pgvector:pg17
VERIFY_DATABASE_URL=postgresql://postgres:devpass@localhost:55432/memory python scripts/verify_context_mcp.py
```

## Private Local Data

Store sensitive personal values in `private/`, which is gitignored.

For local-only links, create `private/links.json` with this shape:

```json
{
  "m1_referral_url": ""
}
```

In production, prefer setting `M1_REFERRAL_URL` as an environment variable.

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
