#!/bin/bash

# All-in-one setup script
echo "====================================="
echo "Django Setup Script"
echo "====================================="
echo ""

# Step 1: Make migrations
echo "[1/3] Creating migrations..."
python manage.py makemigrations
echo ""

# Step 2: Apply migrations
echo "[2/3] Applying migrations..."
python manage.py migrate
echo ""

# Step 3: Start development server
echo "[3/3] Starting development server..."
echo "Server will run at http://127.0.0.1:8000/"
echo "Press Ctrl+C to stop"
echo ""
python manage.py runserver