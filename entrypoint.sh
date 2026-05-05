#!/bin/sh
set -e

# Apply database migrations
echo "Applying database migrations..."
uv run alembic upgrade head

# Execute the main container command
echo "Starting hovorunv2..."
exec "$@"
