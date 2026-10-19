# Self-hosting Certifier

This guide runs Certifier in production from source. There's no official Docker image or one-click install yet: you run two apps behind HTTPS, plus the services they need. The [checklist](#production-checklist) at the end sums it up.

For a local setup to develop on, see [development.md](development.md) instead.

## How it fits together

```
Browser ──HTTPS──▶ Frontend (Next.js) ──▶ API (Django + gunicorn) ──▶ PostgreSQL
   │                                        │   ▲
   │                                        ▼   │
   │                                      Redis ◀── Celery worker
   │                                        │
   └──── uploads and downloads ────▶ S3-compatible bucket (and Cloudinary, if used)
```

- **The browser never calls the API.** Every API call is made by the frontend's server, which keeps the user's session. So the API can sit on a private network that only the frontend reaches. Expose it publicly only if you want the Django admin (`/admin/`) or the API docs (`/api/schema/swagger-ui/`) from outside.
- **Files go straight between the browser and the bucket**, through short-lived signed links the API hands out.
- **The Celery worker** renders certificates and builds export zips. Without it, generation and exports sit in the queue forever.

## What you need

| Service | Notes |
|---|---|
| PostgreSQL | Tested with 17. |
| Redis | Celery's queue, and a shared cache for rate limits. |
| S3-compatible bucket | AWS S3, Backblaze B2, MinIO and similar. Holds uploaded templates, CSVs, export zips and (optionally) certificates. |
| Cloudinary account | Optional: only if certificates are stored there (`CERTIFICATE_STORAGE=cloudinary`, the default). Set `CERTIFICATE_STORAGE=s3` to keep everything in the bucket. |
| SMTP server | For email verification. Students must verify their email before certificates can be issued to them. |
| Python 3.13 + [uv](https://docs.astral.sh/uv/) | For the API and the worker. |
| [Bun](https://bun.sh) | To build and run the frontend. |
| A reverse proxy with HTTPS | nginx, Caddy, a cloud load balancer. Both apps expect to sit behind one. |

## 1. Storage bucket

Create a private bucket (no public access) and an access key limited to it. Browsers upload to and download from it directly, so it needs a CORS rule for your frontend's address:

```json
[
  {
    "AllowedOrigins": ["https://certifier.example.com"],
    "AllowedMethods": ["PUT", "GET"],
    "AllowedHeaders": ["Content-Type"]
  }
]
```

Uploads are limited to 10 MB (`MAX_FILE_SIZE` in `config/settings.py`), and signed links last an hour.

## 2. Backend (API and worker)

### Settings

Django doesn't read `.env` files: pass these as environment variables (a systemd `EnvironmentFile`, your container platform's secrets, and so on). `.env.sample` lists them all with comments. Give the API and the worker **the same** settings.

**Required**

| Variable | Value |
|---|---|
| `SECRET_KEY` | A long random string: `python -c "import secrets; print(secrets.token_urlsafe(50))"`. Keep it secret; changing it logs everyone out and invalidates verification links. |
| `ALLOWED_HOSTS` | The hostname(s) the API is reached at, comma-separated, e.g. `api.internal` or `api.example.com`. |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` | The database. |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Redis, e.g. `redis://redis:6379/0`. |
| `REDIS_CACHE_URL` | Redis on a different database number, e.g. `redis://redis:6379/1`. Without it each gunicorn process keeps its own rate-limit counts. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_REGION_NAME` | The bucket. Add `AWS_S3_ENDPOINT_URL` for anything other than AWS. |
| `CERTIFICATE_STORAGE` | `s3` (same bucket) or `cloudinary` (then also `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`). Don't use `local` in production: local files are only served with `DEBUG` on. |
| `FRONTEND_URL` | The frontend's public address, e.g. `https://certifier.example.com`. It goes into email links and into the verification QR code printed on every certificate, so **choose it for good**: certificates already issued keep the old address. |
| `EMAIL_BACKEND` | `django.core.mail.backends.smtp.EmailBackend`, with `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` and `DEFAULT_FROM_EMAIL`. Without it, emails only appear in the log. |
| `NUM_PROXIES` | How many proxies add to `X-Forwarded-For` before a request reaches Django: `1` if the frontend calls the API directly, plus one for each proxy in front of the API. Rate limits need it to see the real visitor. |
| `TRUST_PROXY_HTTPS` | `1` when a proxy in front of the API terminates HTTPS and sets `X-Forwarded-Proto`. |

**Leave unset:** `DEBUG` (it must stay off) and `SESSION_COOKIE_SECURE` (on by default).

**Optional:** `TIME_ZONE` (default `Asia/Kolkata`), `CSRF_TRUSTED_ORIGINS` (if the admin is served under a different host than Django sees), and the Google and Canva settings in [section 4](#4-optional-integrations).

### Install and prepare

```bash
git clone https://github.com/Shantanu700/certifier_reloaded_backend.git
cd certifier_reloaded_backend
uv sync --locked
uv run manage.py makemigrations
uv run manage.py migrate
uv run manage.py collectstatic --noinput
```

> **Keep your migration files.** This project doesn't commit migrations: `makemigrations` writes them into `modules/*/migrations/` on your server. From now on those files are part of your deployment. Keep them on persistent storage, or commit them to your own fork, and never delete them. On each upgrade, `makemigrations` adds only the new changes on top. Generating them from scratch against an existing database breaks its schema history.

### Run

```bash
uv run gunicorn config.wsgi --bind 0.0.0.0:8000 --workers 3
uv run celery -A config worker -l info
```

Run both under a process manager (systemd, supervisord, your container platform) so they restart on failure and on boot. The API serves its own static files (the admin's and Swagger's styling) through WhiteNoise, so the proxy only needs to forward requests.

People choose whether they issue or receive certificates when they sign up. To change an account's role later, use the Django shell (the admin at `/admin/` doesn't list Certifier's own data yet):

```bash
uv run manage.py shell -c "from modules.core.models import User; User.objects.filter(username='asha').update(role='issuer')"
```

## 3. Frontend

```bash
git clone https://github.com/Shantanu700/certifier_reloaded_frontend.git
cd certifier_reloaded_frontend
bun install --frozen-lockfile
NEXT_PUBLIC_API_URL=https://api.internal bun run build
PORT=3000 bun run start
```

- `NEXT_PUBLIC_API_URL` (the API's address, as the frontend's server reaches it) and `NEXT_PUBLIC_GOOGLE_CLIENT_ID` are **built into** the app: set them before `bun run build`, and rebuild after changing them.
- Serve it over HTTPS only. In production the session cookie is marked secure, so logins don't work over plain http.
- Put it behind a proxy that **appends** the visitor's address to `X-Forwarded-For` (nginx below, Vercel and most load balancers do). The frontend passes it on to the API for rate limiting; exposed directly, visitors could fake it.

### Example: nginx in front of both

```nginx
server {
    listen 443 ssl;
    server_name certifier.example.com;
    # ssl_certificate ... (e.g. from Let's Encrypt)

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# Only if you want the admin and API docs reachable from outside:
server {
    listen 443 ssl;
    server_name api.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 1m;   # uploads go to the bucket, not through here
    }
}
```

With this layout the frontend calls the API directly: build it with `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000`, and set `NUM_PROXIES=1`, `TRUST_PROXY_HTTPS=1` and `ALLOWED_HOSTS=127.0.0.1,api.example.com` on the API.

## 4. Optional integrations

- **Google sign-in:** create an OAuth client in Google Cloud Console with your frontend's address as an authorised JavaScript origin. Set its client ID as `GOOGLE_CLIENT_ID` on the API and `NEXT_PUBLIC_GOOGLE_CLIENT_ID` on the frontend (before building).
- **Canva import:** create an integration in Canva's Developer Portal with the scopes `design:meta:read` and `design:content:read`, and register `https://<your frontend>/canva/callback` as its redirect URL. On the API, set `CANVA_CLIENT_ID`, `CANVA_CLIENT_SECRET`, `CANVA_REDIRECT_URI` (that exact URL) and `CANVA_TOKEN_KEY`, a key that encrypts stored Canva tokens: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Changing the key disconnects everyone's Canva account.

## Production checklist

**Settings**
- [ ] `DEBUG` is unset, and `SECRET_KEY` is long, random and stored as a secret.
- [ ] `ALLOWED_HOSTS` lists only the API's real hostnames.
- [ ] `FRONTEND_URL` is the final public address (it's printed into every certificate's QR code).
- [ ] `REDIS_CACHE_URL` is set, so rate limits are shared between gunicorn processes.
- [ ] `NUM_PROXIES` matches your proxy layout, and `TRUST_PROXY_HTTPS=1` if a proxy terminates HTTPS for the API.
- [ ] `CERTIFICATE_STORAGE` is `s3` or `cloudinary`, not `local`.
- [ ] Real SMTP settings, and a verification email arrives.
- [ ] The API and the worker have identical settings.

**Network and storage**
- [ ] Both apps are reached only over HTTPS, through a proxy that appends `X-Forwarded-For`.
- [ ] The API isn't publicly reachable unless you need the admin or API docs there.
- [ ] PostgreSQL and Redis aren't reachable from the internet.
- [ ] The bucket is private, its key can reach only that bucket, and its CORS rule allows only your frontend's address.

**Deploy**
- [ ] `makemigrations`, `migrate` and `collectstatic` ran, and the generated migration files are kept somewhere that survives redeploys.
- [ ] gunicorn and the Celery worker run under a process manager.
- [ ] The frontend was built with the right `NEXT_PUBLIC_API_URL`.
- [ ] Any superuser you created for the admin has a strong password.

**Smoke test**
- [ ] Sign up as an issuer, upload a template, place a few boxes, upload a CSV, generate, then export the zip.
- [ ] Sign up as a student, verify the email, issue a certificate to them, and check they see it.
- [ ] Scan a certificate's QR code: it opens the public verify page on your domain.

**Operations**
- [ ] Database backups run on a schedule, and you've tried a restore.
- [ ] The bucket (and Cloudinary, if used) is backed up or versioned.
- [ ] The kept migration files are backed up with the database.

## Upgrading

```bash
git pull
uv sync --locked
uv run manage.py makemigrations   # adds only what changed, on top of your kept files
uv run manage.py migrate
uv run manage.py collectstatic --noinput
# restart gunicorn and the Celery worker
```

Then rebuild and restart the frontend (`bun install --frozen-lockfile`, `bun run build`, `bun run start`). Read the merged pull requests since your last upgrade first: when a change needs existing data cleaned up before `migrate` (for example a new uniqueness rule), the pull request includes the steps.

## Backups

Back up three things together: the PostgreSQL database, the bucket (plus Cloudinary, if used), and the migration files. A database restored without the matching migration files can't be upgraded cleanly.

## Security notes

- Login is limited to 10 attempts an hour per username, sign-up to 100 an hour per visitor, and the public verify page to 120 a minute per visitor. Someone who knows a username can lock it out of password login for an hour; Google sign-in still works.
- The API docs (`/api/schema/`) describe every endpoint but don't expose data; block them at the proxy if you'd rather not publish them.
- Report vulnerabilities privately, as described in [SECURITY.md](../SECURITY.md).
