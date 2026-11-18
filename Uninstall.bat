@echo off
rem Removes the OrbisFox shortcuts (and the start-with-Windows entry). Your data folder is kept.
python "%~dp0orbisfox.pyw" --remove-shortcuts
pause
