#!/bin/bash
set -e

/opt/venv/bin/python manage.py migrate --noinput

exec "$@"
