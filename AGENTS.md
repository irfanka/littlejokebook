# Little Jokebook

Scraper and AI-driven catalogue of stand-up comedians, their sets, and materials. Scrapes videos, transcribes them, analyzes with AI, and incrementally builds the catalogue. Domain models will evolve frequently.

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
| 5436 | PostgreSQL 18 |

## Temporal Workflows

All background work goes through Temporal (not Celery). Workflows and activities live in `workflows/`. Register them in `worker.py`. Code changes take effect on `docker compose restart worker`.

Activities that run longer than a few seconds must `activity.heartbeat()` and `await asyncio.sleep(0)`.

To trigger workflows: `docker compose exec worker /opt/venv/bin/python <script.py>`

## Django

Settings in `little_jokebook/settings.py`. DB config via `DATABASE_URL` env var. Migrations run automatically on container start. Code hot-reloads.
