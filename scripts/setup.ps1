$ProjectDir = Split-Path -Parent $PSScriptRoot
& "$ProjectDir\.venv\Scripts\Activate.ps1"

# All-in-one setup script
Write-Host "====================================="
Write-Host "Django Setup Script"
Write-Host "====================================="
Write-Host ""

# Step 1: Make migrations
Write-Host "[1/3] Creating migrations..."
python manage.py makemigrations
Write-Host ""

# Step 2: Apply migrations
Write-Host "[2/3] Applying migrations..."
python manage.py migrate
Write-Host ""

# Step 3: Start development server
Write-Host "[3/3] Starting development server..."
Write-Host "Server will run at http://127.0.0.1:8000/"
Write-Host "Press Ctrl+C to stop"
Write-Host ""
python manage.py runserver
