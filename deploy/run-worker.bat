@echo off
:: Service entry for the V2Pulse background worker (NSSM runs this).
cd /d D:\Mahesh\Python\v2pulse
call .\env.bat
venv\Scripts\python.exe -m pulse.worker
