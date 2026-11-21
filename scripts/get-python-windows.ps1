<#
  Downloads the official portable ("embeddable") Python for Windows from python.org into .\runtime\python,
  so OBD-II Diag runs on a PC that has no Python installed.
  Nothing is installed on the system and no administrator rights are needed. Delete the "runtime" folder to remove it.

  Safety: the zip must match the SHA-256 pinned below (the file python.org publishes for this exact version),
  and python.exe is checked for the Python Software Foundation signature.

  Offline / restricted network: download python-3.12.10-embed-amd64.zip yourself and run
      powershell -ExecutionPolicy Bypass -File scripts\get-python-windows.ps1 -ZipPath C:\path\to\the.zip
  Re-install:  add -Force

  Baixa o Python portatil oficial do python.org para .\runtime\python. Nada e instalado no sistema.
#>
param(
  [string]$ZipPath = "",
  [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"      # the progress bar makes Windows PowerShell 5.1 downloads very slow

$Version  = "3.12.10"
$FileName = "python-$Version-embed-amd64.zip"
$Url      = "https://www.python.org/ftp/python/$Version/$FileName"
$Sha256   = "4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3"

$Root    = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root "runtime"
$Dest    = Join-Path $Runtime "python"
$Stage   = Join-Path $Runtime "python.new"
$Work    = Join-Path ([IO.Path]::GetTempPath()) ("obd2dash-" + [Guid]::NewGuid().ToString("N"))

function Say([string]$en, [string]$pt) {
  Write-Host ("  " + $en)
  Write-Host ("  " + $pt) -ForegroundColor DarkGray
}

try {
  if ((Test-Path (Join-Path $Dest "python.exe")) -and -not $Force) {
    Say "Portable Python is already in .\runtime\python (use -Force to reinstall)." "O Python portatil ja esta em .\runtime\python (use -Force para reinstalar)."
    exit 0
  }
  if (-not [Environment]::Is64BitOperatingSystem) {
    throw "This script supports 64-bit Windows only. Install Python 3.8 or newer from https://www.python.org/downloads/ instead."
  }

  New-Item -ItemType Directory -Path $Work -Force | Out-Null
  $zip = Join-Path $Work $FileName

  if ($ZipPath) {
    if (-not (Test-Path $ZipPath)) { throw "File not found: $ZipPath" }
    Copy-Item -LiteralPath $ZipPath -Destination $zip
  } else {
    Say "Downloading Python $Version (11 MB) from python.org ..." "Baixando o Python $Version (11 MB) do python.org ..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
      Invoke-WebRequest -Uri $Url -OutFile $zip -UseBasicParsing
    } catch {
      throw ("Download failed: " + $_.Exception.Message + "`n  Check your internet connection (or proxy), or download the file by hand and use -ZipPath. URL: " + $Url)
    }
  }

  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash
  if ($actual -ne $Sha256) {
    throw "Checksum mismatch: the file is not the expected Python $Version package (got $actual). Nothing was installed."
  }
  Say "Checksum OK." "Checksum conferido."

  if (Test-Path $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
  New-Item -ItemType Directory -Path $Runtime -Force | Out-Null
  Expand-Archive -LiteralPath $zip -DestinationPath $Stage -Force

  $exe = Join-Path $Stage "python.exe"
  $sig = Get-AuthenticodeSignature -FilePath $exe
  if ($sig.Status -eq "Valid" -and $sig.SignerCertificate.Subject -notlike "*Python Software Foundation*") {
    throw "python.exe is signed by an unexpected publisher: $($sig.SignerCertificate.Subject)"
  }
  if ($sig.Status -eq "Valid") { Say "python.exe is signed by the Python Software Foundation." "python.exe assinado pela Python Software Foundation." }

  # Let "python run.py" / "python -m obd2dash" find the application (the embeddable build ignores the current folder).
  $pth = Get-ChildItem -LiteralPath $Stage -Filter "python*._pth" | Select-Object -First 1
  if (-not $pth) { throw "The package has no python*._pth file; unexpected layout." }
  $lines = @(
    ([IO.Path]::GetFileNameWithoutExtension($pth.Name) + ".zip"),
    ".",
    "..\..",
    "",
    "# '..\..' is the project root (this file lives in <project>\runtime\python).",
    "# site-packages is not needed: pyserial is bundled in <project>\vendor.",
    "#import site"
  )
  [IO.File]::WriteAllLines($pth.FullName, $lines, [Text.Encoding]::ASCII)

  if (Test-Path $Dest) { Remove-Item -LiteralPath $Dest -Recurse -Force }
  Move-Item -LiteralPath $Stage -Destination $Dest

  $out = & (Join-Path $Dest "python.exe") -c "import sys, obd2dash; print(sys.version.split()[0])"
  if ($LASTEXITCODE -ne 0) { throw "The portable Python was installed but could not start the application." }
  Say "Portable Python $out is ready in .\runtime\python" "Python portatil $out pronto em .\runtime\python"
  exit 0
}
catch {
  Write-Host ""
  Write-Host ("  ERROR: " + $_.Exception.Message) -ForegroundColor Red
  if ($_.Exception.Message -like "*denied*" -or $_.Exception.Message -like "*being used*") {
    Write-Host "  A file is locked (antivirus or cloud sync such as OneDrive?). Close other programs, or move this folder out of OneDrive, and try again." -ForegroundColor Yellow
  }
  exit 1
}
finally {
  if (Test-Path $Work)  { Remove-Item -LiteralPath $Work  -Recurse -Force -ErrorAction SilentlyContinue }
  if (Test-Path $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue }
}
