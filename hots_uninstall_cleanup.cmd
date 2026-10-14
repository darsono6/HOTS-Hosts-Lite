@echo off
setlocal enabledelayedexpansion

if defined ProgramData (
    set "MACHINE_DIR=%ProgramData%\HOTS Hosts Lite"
) else (
    set "MACHINE_DIR=C:\ProgramData\HOTS Hosts Lite"
)

if defined APPDATA (
    set "USER_DIR=%APPDATA%\HOTS Hosts Lite"
) else (
    set "USER_DIR=C:\HOTS Hosts Lite"
)

set "LOG=%TEMP%\hots_lite_uninstall.log"
set "RESULT_FILE=%TEMP%\hots_lite_uninstall_result_%RANDOM%.json"

REM Argument from the uninstaller: the file with the choices made in the wizard.
set "CHOICES_FILE=%~1"
set "PS_CHOICES=%CHOICES_FILE:'=''%"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [hots_uninstall] Not running as administrator - elevating via UAC... >> "%LOG%"
    powershell -NoProfile -Command "$p = Start-Process -FilePath '%~f0' -ArgumentList '\"%PS_CHOICES%\"' -Verb RunAs -Wait -PassThru; exit $p.ExitCode"
    exit /b %errorlevel%
)

REM --- The "what should we undo?" wizard is shown by the uninstaller before anything is
REM     removed; hosts/firewall/registry cleanup lives in "HOTS Hosts Lite.exe" itself
REM     (--uninstall-apply). This script is only an elevated orchestrator - no password
REM     check here, since this edition has no parental-control feature to protect.

echo [hots_uninstall] ---- %DATE% %TIME% ---- >> "%LOG%"
echo [hots_uninstall] Choices file: %CHOICES_FILE% >> "%LOG%"
if exist "%CHOICES_FILE%" (
    type "%CHOICES_FILE%" >> "%LOG%"
    echo. >> "%LOG%"
) else (
    echo [hots_uninstall] WARNING: choices file not found - nothing selected in the wizard will be applied. >> "%LOG%"
)

echo [hots_uninstall] Applying the choices made in the uninstall wizard... >> "%LOG%"
"%~dp0HOTS Hosts Lite.exe" --uninstall-apply "%CHOICES_FILE%" "%RESULT_FILE%"
echo [hots_uninstall] Cleanup step finished (exit code !errorlevel!). >> "%LOG%"

set "DELETE_DATA=0"
if exist "%RESULT_FILE%" (
    type "%RESULT_FILE%" >> "%LOG%"
    echo. >> "%LOG%"
    findstr /c:"\"delete_data\": true" "%RESULT_FILE%" >nul 2>&1
    if not errorlevel 1 set "DELETE_DATA=1"
    del /f /q "%RESULT_FILE%" >nul 2>&1
) else (
    echo [hots_uninstall] WARNING: no result from the cleanup step - keeping saved configuration to be safe. >> "%LOG%"
)

if "%DELETE_DATA%"=="1" (
    if exist "%MACHINE_DIR%" (
        echo [hots_uninstall] Removing data folder: %MACHINE_DIR% ... >> "%LOG%"
        attrib -R -H -S "%MACHINE_DIR%\*.*" /S /D >nul 2>&1
        rmdir /s /q "%MACHINE_DIR%" >nul 2>&1
        if exist "%MACHINE_DIR%" (
            echo [hots_uninstall] WARNING: could not fully remove %MACHINE_DIR% >> "%LOG%"
        ) else (
            echo [hots_uninstall] Removed %MACHINE_DIR% >> "%LOG%"
        )
    ) else (
        echo [hots_uninstall] Folder %MACHINE_DIR% does not exist - skipping. >> "%LOG%"
    )

    if exist "%USER_DIR%" (
        echo [hots_uninstall] Removing data folder: %USER_DIR% ... >> "%LOG%"
        attrib -R -H -S "%USER_DIR%\*.*" /S /D >nul 2>&1
        rmdir /s /q "%USER_DIR%" >nul 2>&1
        if exist "%USER_DIR%" (
            echo [hots_uninstall] WARNING: could not fully remove %USER_DIR% >> "%LOG%"
        ) else (
            echo [hots_uninstall] Removed %USER_DIR% >> "%LOG%"
        )
    ) else (
        echo [hots_uninstall] Folder %USER_DIR% does not exist - skipping. >> "%LOG%"
    )
) else (
    echo [hots_uninstall] Keeping saved configuration in %MACHINE_DIR% and %USER_DIR%. >> "%LOG%"
)

echo [hots_uninstall] Done. >> "%LOG%"

endlocal
exit /b 0
