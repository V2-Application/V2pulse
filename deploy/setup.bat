@echo off
:: Step 1 of 3. Run as Administrator over RDP. Creates the Python venv and the Snowflake key.
cd /d D:\Mahesh\Python\v2pulse
if not exist env.bat (echo Copy deploy\env.bat.example to env.bat and fill it in first. & pause & exit /b 1)
set PY=C:\Users\administrator2\AppData\Local\Programs\Python\Python311\python.exe
if not exist venv %PY% -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt
if not exist logs mkdir logs
venv\Scripts\python.exe deploy\keygen.py D:\Mahesh\Python\v2pulse\keys
echo.
echo Send the "alter user PULSE_SVC" line above to whoever runs Part B of deploy\snowflake_setup.sql.
pause
