#!/bin/sh
set -e

# only the api container runs migrations, the worker just waits for the db
if [ "$RUN_MIGRATIONS" = "true" ]; then
    echo "running migrations"
    alembic upgrade head
fi

if [ "$SEED_DATA" = "true" ]; then
    python -m app.scripts.seed
fi

exec "$@"
