#!/bin/bash

# Make Django migrations
echo "Creating migrations..."
python manage.py makemigrations

echo ""
echo "Migrations created successfully!"
echo "Run ./scripts/migrate.sh to apply them"