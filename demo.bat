@echo off
rem Try OBD-II Diag without a car: starts the dashboard with the built-in simulator.
call "%~dp0run.bat" --demo %*
exit /b %errorlevel%
