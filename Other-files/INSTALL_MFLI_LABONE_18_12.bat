@echo off
setlocal EnableExtensions
title Install MFLI LabOne 18.12 Python bridge

rem This installer intentionally uses Python 3.7. LabOne 18.12.59443 only
rem published binary wheels for CPython 2.7/3.5/3.6/3.7, not Python 3.11.
set "MFLI_PY_DIR=%LOCALAPPDATA%\NBB\MOKE_MFLI_PY37"
set "MFLI_PY=%MFLI_PY_DIR%\python.exe"
set "MFLI_TMP=%TEMP%\MOKE_MFLI_18_12_INSTALL"
set "PY_INSTALLER=%MFLI_TMP%\python-3.7.9-amd64.exe"
set "ZHINST_WHEEL=%MFLI_TMP%\zhinst-18.12.59443-cp37-cp37m-win_amd64.whl"
set "ZHINST_SHA256=f92e32a5766308d2c62311eaf2c8205ca76ed304f6c0511443f4e326ef203040"

echo.
echo ================================================================
echo   MOKE MFLI / LabOne 18.12.59443 compatibility installer
echo ================================================================
echo Target Python: %MFLI_PY%
echo.

if not exist "%MFLI_TMP%" mkdir "%MFLI_TMP%"
if errorlevel 1 goto :failed

if not exist "%MFLI_PY%" (
    echo [1/5] Downloading official CPython 3.7.9 x64 installer...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.7.9/python-3.7.9-amd64.exe' -OutFile '%PY_INSTALLER%'"
    if errorlevel 1 goto :download_failed

    echo [2/5] Installing isolated Python 3.7.9 for MFLI only...
    "%PY_INSTALLER%" /quiet InstallAllUsers=0 TargetDir="%MFLI_PY_DIR%" Include_pip=1 Include_launcher=0 Include_test=0 Include_doc=0 Include_tcltk=0 PrependPath=0 Shortcuts=0 SimpleInstall=1
    if errorlevel 1 goto :python_failed
    if not exist "%MFLI_PY%" goto :python_failed
) else (
    echo [1/5] Isolated Python 3.7 already exists; reusing it.
    echo [2/5] Python installation skipped.
)

echo [3/5] Installing the last Python 3.7-compatible NumPy...
"%MFLI_PY%" -m pip install --disable-pip-version-check --no-cache-dir --upgrade "numpy==1.21.6"
if errorlevel 1 goto :package_failed

echo [4/5] Downloading exact official zhinst 18.12.59443 CPython 3.7 wheel...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $release=Invoke-RestMethod 'https://pypi.org/pypi/zhinst/18.12.59443/json'; $file=$release.urls | Where-Object { $_.filename -eq 'zhinst-18.12.59443-cp37-cp37m-win_amd64.whl' } | Select-Object -First 1; if ($null -eq $file) { throw 'Official CPython 3.7 x64 wheel not found on PyPI' }; Invoke-WebRequest -UseBasicParsing $file.url -OutFile '%ZHINST_WHEEL%'; $actual=(Get-FileHash -Algorithm SHA256 '%ZHINST_WHEEL%').Hash.ToLowerInvariant(); if ($actual -ne '%ZHINST_SHA256%') { throw ('Wheel SHA256 mismatch: ' + $actual) }; Write-Host ('Verified wheel SHA256: ' + $actual)"
if errorlevel 1 goto :download_failed

echo [5/5] Installing and validating zhinst 18.12.59443...
"%MFLI_PY%" -m pip install --disable-pip-version-check --no-cache-dir --force-reinstall --no-deps "%ZHINST_WHEEL%"
if errorlevel 1 goto :package_failed

"%MFLI_PY%" -c "import sys, pkg_resources, numpy, zhinst, zhinst.utils; v=pkg_resources.get_distribution('zhinst').version; assert sys.version_info[:2] == (3, 7), sys.version; assert v == '18.12.59443', v; assert numpy.__version__ == '1.21.6', numpy.__version__; print('Verified Python:', sys.version); print('Verified NumPy:', numpy.__version__); print('Verified zhinst:', v, zhinst.__file__)"
if errorlevel 1 goto :package_failed

setx MOKE_MFLI_PYTHON "%MFLI_PY%" >nul
echo.
echo ================================================================
echo SUCCESS
echo Python : %MFLI_PY%
echo zhinst : 18.12.59443
echo.
echo Close any running MOKE program, start MOKE_NH_dev.py again, then
echo connect Zurich MFLI. No reboot is required.
echo ================================================================
echo.
pause
exit /b 0

:download_failed
echo.
echo ERROR: Download failed. Check internet access, TLS, proxy, and firewall.
goto :failed

:python_failed
echo.
echo ERROR: Python 3.7.9 installation failed.
goto :failed

:package_failed
echo.
echo ERROR: zhinst 18.12.59443 installation or import validation failed.
goto :failed

:failed
echo.
echo Installation did not complete. Nothing was changed in Python 3.11.
pause
exit /b 1
