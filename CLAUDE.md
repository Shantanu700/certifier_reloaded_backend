# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependencies are managed by `uv` (Python 3.13, `pyproject.toml` + `uv.lock`). Everything runs through `uv run`.

```bash
uv sync                                     # install deps
uv run manage.py runserver 0.0.0.0:8000     # dev server (exposed as :8022 by docker-compose)
uv run manage.py runsslserver 0.0.0.0:8001  # HTTPS (django-sslserver-v2); needed because SESSION_COOKIE_SECURE=True
uv run manage.py makemigrations core
uv run manage.py migrate
uv run manage.py createsuperuser
uv run manage.py test                       # all tests
uv run manage.py test modules.core.tests.SomeTestCase.test_method   # single test
uv run manage.py spectacular --file schema.yaml                     # dump OpenAPI schema
uv run celery -A config worker -l info      # certificate generation runs as a background task; needs this running
```

Interactive docs: `/api/schema/swagger-ui/` and `/api/schema/redoc/`.

Human docs live in `docs/`: `development.md` (local setup) and `self-hosting.md` (production, with a checklist). Keep them in step with settings and setup changes. Licence is AGPL-3.0-only; contributions need a DCO sign-off (`CONTRIBUTING.md`).

Two Docker setups:

- **`compose.yaml` (repo root)** — the self-contained one for contributors: `docker compose up` runs Postgres, Redis, Floci (local AWS; S3 at `http://localhost.floci.io:4566`), the API (`runserver`, port `API_PORT`, default 8000) and a Celery worker, all configured inline (no `.env`). A one-shot `init` service runs `create_bucket` (`modules/core/management/commands/`), `makemigrations`, `migrate` and `seed_fixtures --if-empty`. `localhost.floci.io` resolves to 127.0.0.1 in public DNS and is a network alias of the Floci container, so presigned URLs work from both the browser and the containers. The `Dockerfile` holds only dependencies (`/opt/venv`); the source is mounted.
- **`docker/nonprod/docker-compose.yaml`** — the maintainer's VS Code devcontainer. The container idles (`sleep`); you start the server yourself. It reads `.env` (only when the container is *created* — restart doesn't reload it), joins the external `dev_db_network` and expects Postgres and Redis already running on it.

**Migrations are gitignored** (`**/migrations/**`), so a fresh clone must `makemigrations` before `migrate`.

## Architecture

Two layers: `base/` is a hand-rolled mini-framework over DRF; `modules/<app>/` holds actual features. Almost every convention lives in `base/` — read it before adding a view, serializer or model.

`config/` is the Django project (settings, urls, ASGI/WSGI). `config/urls.py` mounts each module's versioned URLConf under a prefix (`core/` → `modules/core/api/v1/urls.py`). New modules: create `modules/<app>/`, add `"modules.<app>"` to `LOCAL_APPS`, and `include()` its `api/v1/urls.py`. The `api/`, `api/v1/` dirs are namespace packages (no `__init__.py`) — keep it that way.

### Views — `base/views.BaseAV`

Subclass `BaseAV`, not `APIView`. It changes three things:

- **`__init_subclass__`** wraps every HTTP handler with `extend_base_schema` (`base/decorators.py`), which injects the 409 / 418 / 401 error responses into the OpenAPI schema automatically. Handlers may still carry their own `@extend_schema`; it is respected.
- **`finalize_response`** lets a handler return a bare `dict`, `list`, `str`, or pydantic `BaseModel`. It is wrapped in a `Response` with the status from `base/constants.STATUS_MAPPING` (post → 201, everything else → 200). Returning an explicit `Response` still works and bypasses this.
- **`authentication`** — class attribute, either `True`/`False` or a per-method dict (`{"post": False}`). It is the single source of truth: `APIAuthenticationPermission` reads it to gate the request, and the schema decorator reads it to decide whether to document a 401 or mark the operation as unauthenticated. Do not touch `permission_classes` to make an endpoint public.

Use `self.fail("message")` to raise a user-facing error and `self.success("message")` for the `{"msg": ...}` shape. Annotate response bodies with `@extend_response(SomeSerializer())` (`base/decorators.py`) rather than spelling out `responses=`. `permission_denied` is overridden to raise `PermissionException` instead of DRF's default, so 401s carry the same `message`/`code` shape as other errors.

### Errors

`base/exceptions.exception_handler`, wired as `REST_FRAMEWORK.EXCEPTION_HANDLER`, is the DRF-wide handler. `CustomError` → **409** ("message is safe to show the user verbatim"), `SerializerError` → **418** (validation failures), `PermissionException` → **401**. Django `ValidationError` → 409. Any other `APIException` has its `.detail` normalized into the same `{"msg": ...}` shape.

### Serializers — `base/serializers.BaseSerializer`

`ModelSerializer` subclass with three extras:

- runtime `fields=[...]` / `exclude=[...]` init kwargs for per-call field subsetting — `ModelBase.BASE_MODEL_FIELDS` (`id`, `status`, `created_at`, `updated_at`) are always kept regardless of `exclude`.
- `file_fields` — names rendered as `{"url", "uuid"}` instead of a raw FK; `@extend_response` also injects `FileObjectSerializer` into the schema for them.
- `dynamic_keys` — flatten related attributes into top-level read-only fields, either `"a.b"` (becomes `a_b`) or `{"name", "source", "type"}`.

### Models — `base/model.ModelBase`

All models inherit `ModelBase`: `uuid` (uuid6), `status`, `created_at`, `updated_at`. **Deletion is soft** — set `status = Status.DELETED` (`base/enums.py`); `DeleteStatusManager` filters those rows out of the default manager, so a hard `.delete()` breaks the convention. FKs default to `DEFAULT_ON_DELETE` (`DO_NOTHING`) from `base/defaults.py`. `DropdownBase` is the self-referencing label/parent tree used by `MasterDropdown`.

### Auth

`AUTH_USER_MODEL = "core.User"` — a custom user model (`ModelBase` + `AbstractUser`, keyed on `username`), with its own `USER_MODEL_FIELDS` tuple used to exclude auth internals (`is_superuser`, `groups`, `password`, ...) from serializers, and `UserManager` layered on the soft-delete manager. Session authentication only; `LoginAV` (`modules/core/api/v1/views.py`) takes a Basic `Authorization` header (base64 `username:password`, decoded manually — not a JSON body): `GET` = whoami, `POST` = login (calls `django.contrib.auth.login`), `DELETE` = logout. Only the frontend's server calls the API with a session (it forwards the session id it keeps in its own cookie), so `base/authentication.ServerSessionAuthentication` skips DRF's CSRF check; Django's `CsrfViewMiddleware` still protects the admin. There is no CORS: no browser calls the API cross-origin.

`ATOMIC_REQUESTS = True`: every request is one transaction, so raising (including `self.fail`) rolls back the whole request.

### Current module inventory

`core` endpoints: `core/login/` (`LoginAV`), `core/register/` (`RegisterAV`, logs the new user in), `core/google/` (`GoogleAuthAV`: verifies a Google ID token against `GOOGLE_CLIENT_ID`, creates or reuses the user by `google_id`, and refuses to auto-link to an existing account with the same email) and `core/upload-url/` (`PresignUploadAV`). `DropDownSerializer` exists in `modules/core/api/v1/serializers.py` but has no corresponding view or URL — don't assume dropdown CRUD exists.

### Notable settings (`config/settings.py`)

- `REST_FRAMEWORK.DEFAULT_SCHEMA_CLASS = base.openapi.BaseSchema` — the `AutoSchema` subclass behind the OpenAPI generation.
- Session cookies are `HTTPONLY`, `SAMESITE=Lax`, `SECURE=True` — Lax keeps them off cross-site requests (the CSRF defence for the API); `SECURE` is why logging into the backend directly (admin, Swagger) needs `runsslserver`.
- `CSRF_TRUSTED_ORIGINS` (comma-separated, from `.env`) is only needed when the admin sits behind a proxy under another host.
- Production serving: gunicorn (`config.wsgi`) with WhiteNoise middleware serving `STATIC_ROOT` (`staticfiles/`, filled by `collectstatic`). `TRUST_PROXY_HTTPS=1` sets `SECURE_PROXY_SSL_HEADER` for a TLS-terminating proxy; `TIME_ZONE` comes from the environment (default `Asia/Kolkata`).
- `CERTIFICATE_STORAGE` (`cloudinary` default, `s3` or `local`) is where rendered certificates go; `IssuanceService.storage_provider` can override it per instance (the seed uses `local`). `SESSION_COOKIE_SECURE` defaults on; compose turns it off (plain http).
- `DEBUG` and `ALLOWED_HOSTS` come from `.env`; `DEBUG` is off unless set (`DEBUG=1` for dev — media serving and `seed_fixtures` need it).
- Rate limits (`base/throttles.py`, rates in `REST_FRAMEWORK.DEFAULT_THROTTLE_RATES`): login per username tried (`LoginRateThrottle`), sign-up and public verify per visitor address (`AddressRateThrottle` + `throttle_scope`). Every request arrives from the frontend's server, so the visitor address comes from the `X-Forwarded-For` it forwards, trusted only per `NUM_PROXIES` in `.env`. Limits live in the cache: per process unless `REDIS_CACHE_URL` is set.
- File-upload limits for `UploadFile` (`MAX_FILE_SIZE` = 10 MB, `ALLOWED_IMG_TYPES`, `ALLOWED_CSV_TYPES`) are enforced at the settings level.

### Tooling caveat

Lint is ruff (a pinned dev dependency), limited on purpose to `E4`/`E7`/`E9`/`F` in `pyproject.toml`: run `uv run ruff check .`. There's no formatter check yet (`ruff format` would rewrite ~35 files). CI (`.github/workflows/ci.yml`) runs ruff, `makemigrations` (migrations aren't committed), `check` and the tests against Postgres on every PR. Tests live in each module's `tests.py` (`modules/core`, `modules/certificates`) and run with `uv run manage.py test modules.<app>.tests`.
