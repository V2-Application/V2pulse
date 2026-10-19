@echo off
:: Service entry for the V2Pulse web service (NSSM runs this). Reads env.bat on every start.
cd /d D:\Mahesh\Python\v2pulse
call .\env.bat
set TLS=
if not "%PULSE_TLS_CERT%"=="" set TLS=--ssl-certfile "%PULSE_TLS_CERT%" --ssl-keyfile "%PULSE_TLS_KEY%"
venv\Scripts\python.exe -m uvicorn pulse.app:app --host 0.0.0.0 --port %PULSE_PORT% --workers 2 --no-proxy-headers %TLS%
