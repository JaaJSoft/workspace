#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../.venv/Scripts/activate"

# Make Django migrations
echo "Creating migrations..."
python manage.py makemigrations

echo ""
echo "Migrations created successfully!"
echo "Run ./scripts/migrate.sh to apply them"