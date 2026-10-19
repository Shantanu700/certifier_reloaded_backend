# Development setup

Certifier is two repositories that run side by side:

- **Backend** (this repo): the Django API, Celery tasks and storage.
- **Frontend**: [certifier_reloaded_frontend](https://github.com/Shantanu700/certifier_reloaded_frontend), a Next.js app. The browser only talks to the frontend; the frontend's server calls the API.

## Quick start with Docker

You need Docker and [Bun](https://bun.sh).

**1. Backend.** From this repo's root:

```bash
docker compose up
```

The first run builds an image and takes a few minutes. When the `web` container logs `Starting development server`, the API is on <http://localhost:8000>.

**2. Frontend.** In the frontend repo:

```bash
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" >> .env.local
bun install
bun dev
```

**3. Sign in** at <http://localhost:3000> with the demo data:

| Login | Has |
|---|---|
| `demo_issuer` | Three certificate templates and their issued certificates |
| `student01` … `student50` | The certificates issued to them |

The password for all of them is `demo-password`.

### What's running

| Service | What it is |
|---|---|
| `postgres` | The database (data kept in a Docker volume) |
| `redis` | Celery's queue and the rate-limit cache |
| `floci` | [Floci](https://github.com/floci-io/floci), a local AWS standing in for S3, on port 4566 |
| `init` | Runs once per `up`: creates the storage bucket, makes and applies migrations, loads the demo data if it isn't there |
| `web` | The API (`runserver`), reloading when you edit code |
| `worker` | The Celery worker that renders and exports certificates |

### Day to day

- **Code changes** apply straight away: the source is mounted into the containers. After changing dependencies (`pyproject.toml`), run `docker compose up --build`.
- **Port 8000 taken?** `API_PORT=8040 docker compose up`, and use that port in the frontend's `NEXT_PUBLIC_API_URL`.
- **Run a command:** `docker compose exec web python manage.py <command>`, for example `createsuperuser` for the Django admin at <http://localhost:8000/admin/>.
- **Start over:** `docker compose down -v` deletes the database and stored files; the next `up` recreates the demo data.
- **Fresh demo data only:** `docker compose exec web python manage.py seed_fixtures --reset` replaces the demo issuer's templates and certificates, and leaves everything else alone.

## Without Docker

You need Python 3.13 with [uv](https://docs.astral.sh/uv/), PostgreSQL, Redis and an S3-compatible bucket. For the bucket, either use a real one or run Floci alone (`docker run -p 4566:4566 floci/floci:2.3.0`) and set `AWS_S3_ENDPOINT_URL=http://localhost.floci.io:4566`.

```bash
cp .env.sample .env          # fill in the values; set DEBUG=1
set -a; source .env; set +a  # Django doesn't read .env by itself
uv sync
uv run manage.py create_bucket      # only for a local S3 emulator: creates the bucket and its CORS rule
uv run manage.py makemigrations     # migrations aren't committed
uv run manage.py migrate
uv run manage.py seed_fixtures      # optional demo data; prints the logins
```

Then, in separate terminals:

```bash
uv run manage.py runserver 0.0.0.0:8000   # the API over http (SESSION_COOKIE_SECURE=0 to use the admin)
uv run manage.py runsslserver 0.0.0.0:8001  # or over https with a self-signed certificate
uv run celery -A config worker -l info    # needed to generate and export certificates
```

The maintainer works in a VS Code devcontainer (`docker/nonprod/`), which expects Postgres and Redis on an external Docker network; the setups above don't need it.

## Frontend

```bash
bun install
bun dev                 # http://localhost:3000
bun run lint
bun test
bun run build           # production build; also the type check
bun run openapi-ts      # regenerate src/client/ after the API changes
```

`NEXT_PUBLIC_API_URL` in `.env.local` points the app at the API; unset, it uses `https://localhost:8022` (the maintainer's devcontainer). `bun run openapi-ts` reads the schema from the dev backend, or from `OPENAPI_INPUT` if set (for example `OPENAPI_INPUT=http://localhost:8000/api/schema/`). Never edit `src/client/` by hand.

## Optional integrations

All of these stay switched off until configured; nothing else depends on them.

- **Google sign-in:** create an OAuth client in Google Cloud Console, set `GOOGLE_CLIENT_ID` on the backend and the same value as `NEXT_PUBLIC_GOOGLE_CLIENT_ID` in the frontend's `.env.local`, and add `http://localhost:3000` to its authorised JavaScript origins.
- **Canva import:** create an integration in Canva's Developer Portal with the scopes `design:meta:read` and `design:content:read`, and register `http://127.0.0.1:3000/canva/callback` as a redirect URL. Then set `CANVA_CLIENT_ID`, `CANVA_CLIENT_SECRET`, `CANVA_REDIRECT_URI` (that same URL) and `CANVA_TOKEN_KEY` (generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
- **Email:** without `EMAIL_BACKEND`, verification emails are printed in the API's log, which is fine for development.

With Docker, add these to the `environment` block in `compose.yaml` (keep real keys out of commits) or to a `compose.override.yaml`.

## Tests, lint and CI

```bash
uv run ruff check .
uv run manage.py test modules.core.tests modules.certificates.tests
# or inside the compose stack:
docker compose exec web python manage.py test modules.core.tests modules.certificates.tests
```

Use the module paths: a bare `manage.py test` doesn't find the tests in this layout. CI (`.github/workflows/ci.yml` in each repo) runs the same checks on every pull request.

## Troubleshooting

- **Uploads or images fail in the browser while offline:** the browser looks up `localhost.floci.io` in public DNS. Add `127.0.0.1 localhost.floci.io` to `/etc/hosts`.
- **Port 4566 is in use:** Floci needs exactly that port, because it's part of the address in upload links. Stop whatever else uses it.
- **A setting in `.env` doesn't take effect in the devcontainer:** the container reads `.env` only when it's created. Rebuild the container; restarting it isn't enough.
- **`manage.py` refuses to run `seed_fixtures` or `create_bucket`:** both only run with `DEBUG=1`.
