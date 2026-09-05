# Arranque de sesion. Ejecutar:  .\arrancar.ps1
#
# Las claves NO viven en el repo. Viven en un fichero del perfil de
# usuario, fuera de cualquier alcance de Git. Un .env en la raiz
# funcionaria, pero basta un .gitignore mal editado o un `git add -f`
# para que acabe en GitHub, y una clave que sale de la maquina se
# considera quemada.

$claves = Join-Path $env:USERPROFILE ".laliga-modelo.env"

# 1. Entorno virtual
& "$PSScriptRoot\.venv\Scripts\Activate.ps1"

# 2. Credenciales
if (-not (Test-Path $claves)) {
    Write-Host "No existe $claves" -ForegroundColor Yellow
    Write-Host "Crealo con este contenido (una por linea, sin comillas):"
    Write-Host "  FOOTBALL_DATA_KEY=..."
    Write-Host "  ANTHROPIC_API_KEY=..."
    Write-Host "  TELEGRAM_TOKEN=..."
    Write-Host "  TELEGRAM_CHAT_ID=..."
    return
}

Get-Content $claves | ForEach-Object {
    $linea = $_.Trim()
    if ($linea -and -not $linea.StartsWith("#")) {
        $partes = $linea -split "=", 2
        if ($partes.Count -eq 2) {
            Set-Item -Path "env:$($partes[0].Trim())" -Value $partes[1].Trim()
        }
    }
}

# 3. Comprobacion. Se enseña la longitud, nunca el valor.
$esperadas = @(
    "FOOTBALL_DATA_KEY",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_TOKEN",
    "TELEGRAM_CHAT_ID"
)

Write-Host ""
foreach ($v in $esperadas) {
    $valor = [Environment]::GetEnvironmentVariable($v)
    if ($valor) {
        Write-Host ("  {0,-20} OK ({1} caracteres)" -f $v, $valor.Length) -ForegroundColor Green
    } else {
        Write-Host ("  {0,-20} FALTA" -f $v) -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Listo. Comandos habituales:" -ForegroundColor Cyan
Write-Host "  python -m src.ingest.fixtures            calendario"
Write-Host "  python -m src.content.jornada_json       JSON de la semana"
Write-Host "  python -m src.evaluate.auditoria         JSON del lunes"
Write-Host "  python -m src.deliver.telegram F1        borradores a Telegram"
Write-Host ""