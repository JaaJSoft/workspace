$ProjectDir = Split-Path -Parent $PSScriptRoot
& "$ProjectDir\.venv\Scripts\Activate.ps1"

# Run Django development server
Write-Host "Starting Django development server..."
python manage.py runserver
