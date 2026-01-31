$ProjectDir = Split-Path -Parent $PSScriptRoot
& "$ProjectDir\.venv\Scripts\Activate.ps1"

# Apply Django migrations
Write-Host "Applying migrations..."
python manage.py migrate

Write-Host ""
Write-Host "Migrations applied successfully!"
