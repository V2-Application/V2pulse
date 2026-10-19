@echo off
:: Step 3 of 3. Run as Administrator. Installs the two boot-start services and opens the port.
cd /d D:\Mahesh\Python\v2pulse
call .\env.bat
set NSSM=D:\Mahesh\Python\tools\nssm-2.24-101-g897c7ad\win64\nssm.exe
%NSSM% install V2Pulse D:\Mahesh\Python\v2pulse\deploy\run-api.bat
%NSSM% set V2Pulse AppDirectory D:\Mahesh\Python\v2pulse
%NSSM% set V2Pulse DisplayName "V2Pulse intranet portal"
%NSSM% set V2Pulse DependOnService V2PulseDB
%NSSM% set V2Pulse AppStdout D:\Mahesh\Python\v2pulse\logs\api.log
%NSSM% set V2Pulse AppStderr D:\Mahesh\Python\v2pulse\logs\api.log
%NSSM% set V2Pulse AppRotateFiles 1
%NSSM% set V2Pulse AppRotateBytes 10485760
%NSSM% set V2Pulse Start SERVICE_AUTO_START
%NSSM% install V2PulseWorker D:\Mahesh\Python\v2pulse\deploy\run-worker.bat
%NSSM% set V2PulseWorker AppDirectory D:\Mahesh\Python\v2pulse
%NSSM% set V2PulseWorker DisplayName "V2Pulse background jobs"
%NSSM% set V2PulseWorker DependOnService V2PulseDB
%NSSM% set V2PulseWorker AppStdout D:\Mahesh\Python\v2pulse\logs\worker.log
%NSSM% set V2PulseWorker AppStderr D:\Mahesh\Python\v2pulse\logs\worker.log
%NSSM% set V2PulseWorker AppRotateFiles 1
%NSSM% set V2PulseWorker AppRotateBytes 10485760
%NSSM% set V2PulseWorker Start SERVICE_AUTO_START
netsh advfirewall firewall add rule name="V2Pulse %PULSE_PORT%" dir=in action=allow protocol=TCP localport=%PULSE_PORT%
%NSSM% start V2Pulse
%NSSM% start V2PulseWorker
%NSSM% status V2Pulse
%NSSM% status V2PulseWorker
pause
