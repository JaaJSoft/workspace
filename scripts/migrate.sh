#!/bin/bash

# Apply Django migrations
echo "Applying migrations..."
python manage.py migrate

echo ""
echo "Migrations applied successfully!"