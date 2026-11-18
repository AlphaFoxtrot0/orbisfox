@echo off
rem Opens OrbisFox. Add --background to start it without a window.
where pythonw >nul 2>nul && (start "" pythonw "%~dp0orbisfox.pyw" %* & exit /b 0)
where pyw >nul 2>nul && (start "" pyw -3 "%~dp0orbisfox.pyw" %* & exit /b 0)
echo OrbisFox needs Python 3.10 or newer: https://www.python.org/downloads/
echo Tick "Add python.exe to PATH" while installing, then run this again.
pause
