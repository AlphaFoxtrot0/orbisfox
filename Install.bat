@echo off
rem Creates OrbisFox shortcuts on the desktop and in the Start menu.
where python >nul 2>nul || (echo OrbisFox needs Python 3.10 or newer: https://www.python.org/downloads/ & pause & exit /b 1)
python "%~dp0orbisfox.pyw" --install-shortcuts
echo.
echo Done. Open OrbisFox from the desktop shortcut.
pause
