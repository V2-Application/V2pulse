@echo off
:: After new code has been copied to the share: install pinned packages, migrate, restart. Run as Administrator.
cd /d D:\Mahesh\Python\v2pulse
call .\env.bat
set NSSM=D:\Mahesh\Python\tools\nssm-2.24-101-g897c7ad\win64\nssm.exe
%NSSM% stop V2PulseWorker
%NSSM% stop V2Pulse
venv\Scripts\python.exe -m pip install -r requirements.txt -q
venv\Scripts\python.exe -m pulse.migrate
%NSSM% start V2Pulse
%NSSM% start V2PulseWorker
%NSSM% status V2Pulse
%NSSM% status V2PulseWorker
pause
