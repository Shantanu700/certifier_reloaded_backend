# Contributing

Thanks for wanting to help. Certifier is two repositories: this Django API and the [Next.js frontend](https://github.com/Shantanu700/certifier_reloaded_frontend). Most features touch both.

## Getting set up

Follow [docs/development.md](docs/development.md). With Docker, `docker compose up` gives you a working backend with demo data in a few minutes.

[CLAUDE.md](CLAUDE.md) explains the backend's conventions: the `base/` mini-framework, soft deletes, error shapes. Read it before adding a view, serializer or model.

## Making a change

1. For anything bigger than a small fix, open an issue first so we can agree on the approach.
2. Branch from `main` and keep each pull request to one change.
3. Run the checks CI runs:
   ```bash
   uv run ruff check .
   uv run manage.py test modules.core.tests modules.certificates.tests
   ```
   (frontend: `bun run lint`, `bun test`, `bun run build`).
4. Add or update tests for behaviour you change.
5. Don't commit migration files. They're gitignored on purpose: each environment generates its own with `makemigrations`. If a change needs existing data cleaned up first, describe it in the pull request.
6. If the API changes, say so in the pull request: the frontend regenerates its client from the schema.

Write commit messages as a short summary line in the imperative ("Add search to the template list"), with detail in the body if it helps.

## Sign your commits (DCO)

Every commit must be signed off, certifying the [Developer Certificate of Origin](https://developercertificate.org/): that you wrote the change, or otherwise have the right to submit it under this project's licence. Add `-s` when committing:

```bash
git commit -s -m "Fix the box editor's snap guides"
```

This adds a `Signed-off-by: Your Name <you@example.com>` line matching your git identity. To sign off commits you've already made: `git rebase --signoff main`.

## Licence

By contributing, you agree that your contributions are licensed under the [GNU AGPL-3.0](LICENSE), the same as the rest of the project.

## Code of conduct and security

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Report security problems privately, as described in [SECURITY.md](SECURITY.md), not in public issues.
