@echo off
rem OBD-II Diag launcher for Windows.
rem   Double-click it, or run:  run.bat [--demo] [--car "Honda Fit 2011"] [--port 8765] [--com COM5] [--no-browser]
rem It uses, in this order: the portable Python in .\runtime, a Python 3.8+ installed on this PC, or (after asking)
rem it downloads the official portable Python from python.org into .\runtime.
rem This file is ASCII on purpose: cmd.exe reads batch files in the OEM code page.
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul 2>nul
title OBD-II Diag
set "RC=0"
set "PY="

if exist "runtime\python\python.exe" set "PY=runtime\python\python.exe"
if defined PY goto :found

rem A Python installed on this PC. The Microsoft Store stub fails the version test and is skipped.
for %%V in (3 3.13 3.12 3.11 3.10 3.9 3.8) do (
  if not defined PY call :try py -%%V
)
if not defined PY call :try python
if not defined PY call :try python3
if defined PY goto :found

echo.
echo  Python 3.8 or newer was not found on this computer.
echo  Python 3.8 ou mais novo nao foi encontrado neste computador.
echo.
echo  A) Install Python from https://www.python.org/downloads/ and open this file again.
echo     Instale o Python em https://www.python.org/downloads/ e abra este arquivo de novo.
echo.
echo  B) Or download a portable copy of Python (about 11 MB, official, from python.org) into the
echo     "runtime" folder next to this file. Nothing is installed on your system, no administrator
echo     rights are needed, and deleting the folder removes it.
echo     Ou baixe uma copia portatil do Python (cerca de 11 MB, oficial, do python.org) para a pasta
echo     "runtime" ao lado deste arquivo. Nada e instalado no sistema, nao precisa de administrador,
echo     e apagar a pasta remove tudo.
echo.
choice /c YSN /m "Download portable Python now? / Baixar o Python portatil agora?"
if errorlevel 3 goto :declined
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\get-python-windows.ps1"
if errorlevel 1 goto :download_failed
if not exist "runtime\python\python.exe" goto :download_failed
set "PY=runtime\python\python.exe"
goto :found

:try
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=%*"
goto :eof

:found
echo.
echo  OBD-II Diag is starting. Close this window to stop it.
echo  O OBD-II Diag esta iniciando. Feche esta janela para encerrar.
echo.
%PY% run.py %*
set "RC=%errorlevel%"
if "%RC%"=="0" goto :end
echo.
echo  The program stopped with an error (code %RC%). Read the message above.
echo  O programa terminou com erro (codigo %RC%). Leia a mensagem acima.
pause
goto :end

:declined
set "RC=1"
echo.
echo  OK. Install Python 3.8 or newer and open this file again.
echo  Ok. Instale o Python 3.8 ou mais novo e abra este arquivo de novo.
goto :end

:download_failed
set "RC=1"
echo.
echo  The portable Python could not be installed. Read the message above, or install Python from
echo  https://www.python.org/downloads/ and open this file again.
echo  Nao foi possivel instalar o Python portatil. Leia a mensagem acima, ou instale o Python em
echo  https://www.python.org/downloads/ e abra este arquivo de novo.
pause

:end
endlocal & exit /b %RC%
