$ProjectDir = Split-Path -Parent $PSScriptRoot
& "$ProjectDir\.venv\Scripts\Activate.ps1"

# Make Django migrations
Write-Host "Creating migrations..."
python manage.py makemigrations

Write-Host ""
Write-Host "Migrations created successfully!"
Write-Host "Run .\scripts\migrate.ps1 to apply them"
