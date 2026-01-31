#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../.venv/Scripts/activate"

# Apply Django migrations
echo "Applying migrations..."
python manage.py migrate

echo ""
echo "Migrations applied successfully!"