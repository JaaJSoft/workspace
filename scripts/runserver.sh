#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../.venv/Scripts/activate"

# Run Django development server
echo "Starting Django development server..."
python manage.py runserver