@echo off
:: Step 2 of 3. Run as Administrator. Creates a dedicated Postgres instance on 127.0.0.1:%PULSE_PG_PORT% (5435) as the
:: Windows service V2PulseDB. Data lives in D:\V2PulseData (outside the code folder, so a redeploy can
:: never touch it). It does NOT touch the existing Postgres instances on 5432 / 5433 / 5434.
setlocal
cd /d D:\Mahesh\Python\v2pulse
call .\env.bat
set PGBIN=D:\Mahesh\Python\v2pulse\pgsql\bin
set PGDATA=D:\V2PulseData\pgdata
if exist "%PGDATA%\PG_VERSION" (echo Database already initialised at %PGDATA%. & goto service)
mkdir D:\V2PulseData 2>nul
> "%TEMP%\v2p_pw.txt" echo %PULSE_PG_SUPERPASS%
"%PGBIN%\initdb.exe" -D "%PGDATA%" -U postgres -A scram-sha-256 --pwfile="%TEMP%\v2p_pw.txt" -E UTF8 --locale=C
del "%TEMP%\v2p_pw.txt"
>> "%PGDATA%\postgresql.conf" echo listen_addresses = '127.0.0.1'
>> "%PGDATA%\postgresql.conf" echo port = %PULSE_PG_PORT%
>> "%PGDATA%\postgresql.conf" echo shared_buffers = 1GB
>> "%PGDATA%\postgresql.conf" echo max_connections = 100
>> "%PGDATA%\postgresql.conf" echo log_min_duration_statement = 2000
:service
sc query V2PulseDB >nul 2>&1 || "%PGBIN%\pg_ctl.exe" register -N V2PulseDB -D "%PGDATA%" -S auto
net start V2PulseDB
set PGPASSWORD=%PULSE_PG_SUPERPASS%
venv\Scripts\python.exe deploy\bootstrap_db.py
if errorlevel 1 (echo Bootstrap failed. & pause & exit /b 1)
venv\Scripts\python.exe -m pulse.migrate
echo Postgres ready on 127.0.0.1:%PULSE_PG_PORT%.
pause
