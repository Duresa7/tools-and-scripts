:: Simple way to open the volume control panel so I can open it using logi ring action
:: Some commands will only work for Windows 11

@echo off
echo Opening the Windows sound control panel...

:: start sndvol & REM Open the volume mixer
start "" mmsys.cpl
if errorlevel 1 (
	echo Failed to open the Windows sound control panel.
	exit /b 1
)
echo Windows sound control panel opened.

:: start ms-settings:sound & REM Open the sound settings in Windows 10/11
:: start ms-settings:app-volume & REM Open the app volume settings in Windows 10/11
