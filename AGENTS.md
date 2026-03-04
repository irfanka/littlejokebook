# Little Jokebook

Scraper and AI-driven catalogue of stand-up comedians, their sets, and materials. Scrapes videos, transcribes them, analyzes with AI, and incrementally builds the catalogue. Domain models will evolve frequently.

## Monorepo Layout

```
backend/   Django + Temporal (API, ingestion pipeline, worker)
web/       Astro (static catalogue site, fetches from Django API)
app/       Expo (future — comedian tool for managing material)
```

## Getting Up to Speed

At the start of each session, run `git log --oneline -20` to understand recent project history and current state.

## Dependencies

The backend uses **uv**. Always add dependencies with `cd backend && uv add <package>`, never by editing `pyproject.toml` directly.

## Dev Environment

```bash
make start    # bring everything up
make stop     # tear it down
make build    # rebuild after dep changes (pyproject.toml)
make logs     # tail all logs
```

| Port | What |
|------|------|
| 9080 | Django (via nginx) |
| 9081 | Temporal UI (via nginx) |
| 9082 | Astro web (via nginx) |
| 5436 | PostgreSQL 18 |

## Temporal Workflows

All background work goes through Temporal (not Celery). Workflows and activities live in `backend/workflows/` and `backend/activities/`. Register them in `backend/worker.py`. Code changes take effect on `docker compose -f docker-compose.dev.yml restart worker`.

Activities that run longer than a few seconds must `activity.heartbeat()` and `await asyncio.sleep(0)`.

To trigger workflows: `docker compose -f docker-compose.dev.yml exec worker /opt/venv/bin/python <script.py>`

## Django

Settings in `backend/little_jokebook/settings.py`. DB config via `DATABASE_URL` env var. Migrations run automatically on container start. Code hot-reloads.
