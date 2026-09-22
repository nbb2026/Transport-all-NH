@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
chcp 65001 >nul

title Magneto-Electro-Optical Transport V3 - Build and Install
echo ============================================================
echo   Magneto-Electro-Optical Transport V3 - Windows x64
echo   Build revision 3.2.0 PY3119 NUITKA413 MINGW64 PCOFIX ROT90 SCANLOG
echo ============================================================
echo.

set "PYTHON_BASE="
set "VENV_DIR=%~dp0.build_venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "SOURCE_FILE=%~dp0Magneto_Electro_Optical_Transport_V3.py"
set "BUILD_DIR=%~dp0build"
set "BUILD_EXE=%BUILD_DIR%\Magneto_Electro_Optical_Transport_V3.exe"
set "OUTPUT_DIR=%~dp0V3_Compiled_Output"
set "OUTPUT_EXE=%OUTPUT_DIR%\Magneto_Electro_Optical_Transport_V3.exe"
set "PCO_DLL_DIR=%VENV_DIR%\Lib\site-packages\pco\win_x64"
set "PCO_DIST_DIR=%BUILD_DIR%\Magneto_Electro_Optical_Transport_V3.dist\pco\win_x64"
set "PCO_TEST_LOG=%BUILD_DIR%\MEO_Transport_V3_pco_self_test.txt"
set "BUILD_REPORT=%BUILD_DIR%\MEO_Transport_V3_compilation_report.xml"
set "OUTPUT_PCO_TEST_LOG=%OUTPUT_DIR%\MEO_Transport_V3_pco_self_test.txt"
set "OUTPUT_REPORT=%OUTPUT_DIR%\MEO_Transport_V3_compilation_report.xml"
set "CHECKSUM_FILE=%OUTPUT_DIR%\Magneto_Electro_Optical_Transport_V3.exe.sha256"
set "PYTHON_INSTALLER_VERSION=3.11.9"
set "PYTHON_INSTALLER=%TEMP%\MEO_Transport_python-3.11.9-amd64.exe"
set "PYTHON_INSTALLER_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"

echo [1/10] Locating 64-bit Python 3.11...
call :locate_python
if not defined PYTHON_BASE (
    echo 64-bit Python 3.11 was not found. Starting automatic installation...
    call :install_python
    if errorlevel 1 goto :failed
    call :locate_python
)
if not defined PYTHON_BASE (
    echo ERROR: Python installation finished, but 64-bit Python 3.11 is unavailable.
    goto :failed
)
"!PYTHON_BASE!" -c "import sys,struct; assert sys.version_info[:2]==(3,11) and struct.calcsize('P')==8"
if errorlevel 1 goto :failed
echo Using: !PYTHON_BASE!

echo.
echo [2/10] Preparing the isolated build environment...
if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -c "import sys,struct; assert sys.version_info[:2]==(3,11) and struct.calcsize('P')==8" >nul 2>&1
    if errorlevel 1 (
        echo Existing build environment is incompatible. Recreating it...
        rmdir /S /Q "%VENV_DIR%"
    )
)
if not exist "%VENV_PYTHON%" (
    "!PYTHON_BASE!" -m venv "%VENV_DIR%"
    if errorlevel 1 goto :failed
)
"%VENV_PYTHON%" -m pip install --upgrade pip wheel
if errorlevel 1 goto :failed
"%VENV_PYTHON%" -m pip install -r "%~dp0requirements_build_v3_2.txt"
if errorlevel 1 goto :failed

echo.
echo [3/10] Checking Python and packaged dependencies...
"%VENV_PYTHON%" -c "import nuitka,numpy,pco,serial,pyvisa,PIL,cv2,clr,zhinst.core,zhinst.utils,MultiPyVu,tkinter; print('Dependency check passed.')"
if errorlevel 1 goto :failed
for %%D in (sc2_cam.dll pco_recorder.dll pco_conv.dll etc_xcite.dll sc2_cl_me4.dll sc2_clhs.dll sc2_genicam.dll sc2_gige.dll) do (
    if not exist "%PCO_DLL_DIR%\%%D" (
        echo ERROR: Required PCO DLL is missing: %PCO_DLL_DIR%\%%D
        goto :failed
    )
)
if not exist "%PCO_DLL_DIR%\genicam\GenApi_MD_VC141_v3_3.dll" (
    echo ERROR: PCO GenICam DLL directory is incomplete.
    goto :failed
)
for /f %%C in ('powershell.exe -NoProfile -Command "(Get-ChildItem -LiteralPath '%PCO_DLL_DIR%' -Filter '*.dll' -Recurse -File).Count"') do set "PCO_SOURCE_DLL_COUNT=%%C"
echo PCO source DLL count: !PCO_SOURCE_DLL_COUNT!
if not defined PCO_SOURCE_DLL_COUNT goto :failed
if !PCO_SOURCE_DLL_COUNT! LSS 27 (
    echo ERROR: Expected at least 27 PCO DLLs, found !PCO_SOURCE_DLL_COUNT!.
    goto :failed
)

echo.
echo [4/10] Running syntax and offline instrument-safety checks...
set "PYTHONDONTWRITEBYTECODE=1"
"%VENV_PYTHON%" -W error -m py_compile "%SOURCE_FILE%"
if errorlevel 1 goto :failed
"%VENV_PYTHON%" -W error -m unittest discover -s "%~dp0tests" -p "test_*.py" -v
if errorlevel 1 goto :failed

echo.
echo [5/10] Compiling the source-free one-file Windows application...
echo The first build can take 30-90 minutes. A long pause near 95 percent
echo is normally C compilation or linking; wait for success or an ERROR.
if not exist "%BUILD_DIR%" mkdir "%BUILD_DIR%"
if exist "%BUILD_EXE%" del /Q "%BUILD_EXE%"
"%VENV_PYTHON%" -m nuitka ^
    --mode=onefile ^
    --mingw64 ^
    --assume-yes-for-downloads ^
    --enable-plugin=tk-inter ^
    --windows-console-mode=disable ^
    --onefile-tempdir-spec="{CACHE_DIR}/NBB/MEO_Transport/V3_320_PY3119_PCOFIX_ROT90_SCANLOG" ^
    --output-dir="%BUILD_DIR%" ^
    --output-filename=Magneto_Electro_Optical_Transport_V3.exe ^
    --windows-icon-from-ico="%~dp0assets\MEO_Transport_V3.ico" ^
    --company-name="NBB Lab" ^
    --product-name="Magneto-Electro-Optical Transport V3" ^
    --file-description="Magneto-electro-optical integrated experiment control" ^
    --file-version=3.2.0.0 ^
    --product-version=3.2.0.0 ^
    --user-package-configuration-file="%~dp0pco.nuitka-package.config.yml" ^
    --include-package=pco ^
    --include-package-data=pco ^
    --include-package=serial ^
    --include-package=pyvisa ^
    --include-package-data=pyvisa ^
    --include-package=PIL ^
    --include-package-data=PIL ^
    --include-package=numpy ^
    --include-package=cv2 ^
    --include-package=pythonnet ^
    --include-module=clr ^
    --include-package=zhinst ^
    --include-package-data=zhinst ^
    --include-package=MultiPyVu ^
    --include-package-data=MultiPyVu ^
    --nofollow-import-to=*.tests ^
    --report="%BUILD_REPORT%" ^
    "%SOURCE_FILE%"
if errorlevel 1 goto :failed
if not exist "%BUILD_EXE%" (
    echo ERROR: Nuitka returned without creating the EXE.
    goto :failed
)

echo.
echo [6/10] Verifying PCO DLLs inside the Nuitka payload...
for %%D in (sc2_cam.dll pco_recorder.dll pco_conv.dll etc_xcite.dll sc2_cl_me4.dll sc2_clhs.dll sc2_genicam.dll sc2_gige.dll) do (
    if not exist "%PCO_DIST_DIR%\%%D" (
        echo ERROR: Nuitka omitted %PCO_DIST_DIR%\%%D
        goto :failed
    )
)
if not exist "%PCO_DIST_DIR%\genicam\GenApi_MD_VC141_v3_3.dll" (
    echo ERROR: Nuitka omitted nested PCO GenICam DLLs.
    goto :failed
)
for /f %%C in ('powershell.exe -NoProfile -Command "(Get-ChildItem -LiteralPath '%PCO_DIST_DIR%' -Filter '*.dll' -Recurse -File).Count"') do set "PCO_PAYLOAD_DLL_COUNT=%%C"
echo PCO payload DLL count: !PCO_PAYLOAD_DLL_COUNT!
if not defined PCO_PAYLOAD_DLL_COUNT goto :failed
if !PCO_PAYLOAD_DLL_COUNT! LSS !PCO_SOURCE_DLL_COUNT! (
    echo ERROR: PCO DLL payload is incomplete.
    goto :failed
)
findstr /I /C:"sc2_cam.dll" "%BUILD_REPORT%" >nul
if errorlevel 1 (
    echo ERROR: Compilation report does not contain sc2_cam.dll.
    goto :failed
)

echo.
echo [7/10] Running the compiled EXE PCO native-library self-test...
if exist "%PCO_TEST_LOG%" del /Q "%PCO_TEST_LOG%"
start "" /wait "%BUILD_EXE%" --pco-dll-self-test
set "PCO_TEST_EXIT=!ERRORLEVEL!"
if exist "%PCO_TEST_LOG%" type "%PCO_TEST_LOG%"
if not "!PCO_TEST_EXIT!"=="0" (
    echo ERROR: The compiled EXE PCO self-test returned !PCO_TEST_EXIT!.
    goto :failed
)
if not exist "%PCO_TEST_LOG%" (
    echo ERROR: The compiled EXE did not create its PCO self-test log.
    goto :failed
)
findstr /I /C:"RESULT: PASS" "%PCO_TEST_LOG%" >nul
if errorlevel 1 (
    echo ERROR: Compiled EXE PCO self-test did not report PASS.
    goto :failed
)

echo.
echo [8/10] Publishing all verified deliverables into one folder...
if exist "%OUTPUT_DIR%" rmdir /S /Q "%OUTPUT_DIR%"
if exist "%OUTPUT_DIR%" (
    echo ERROR: Close any running V3 EXE or file opened from %OUTPUT_DIR%, then retry.
    goto :failed
)
mkdir "%OUTPUT_DIR%"
if errorlevel 1 goto :failed
copy /Y "%BUILD_EXE%" "%OUTPUT_EXE%" >nul
if errorlevel 1 goto :failed
copy /Y "%PCO_TEST_LOG%" "%OUTPUT_PCO_TEST_LOG%" >nul
if errorlevel 1 goto :failed
copy /Y "%BUILD_REPORT%" "%OUTPUT_REPORT%" >nul
if errorlevel 1 goto :failed

echo.
echo [9/10] Creating the Desktop shortcut...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_desktop_shortcut.ps1" -ExePath "%OUTPUT_EXE%"
if errorlevel 1 goto :failed

echo.
echo [10/10] Verifying the published files...
fc /B "%BUILD_EXE%" "%OUTPUT_EXE%" >nul
if errorlevel 1 (
    echo ERROR: Published EXE differs from the verified build EXE.
    goto :failed
)
powershell.exe -NoProfile -Command "$h=(Get-FileHash -Algorithm SHA256 -LiteralPath '%OUTPUT_EXE%').Hash.ToLowerInvariant(); Set-Content -LiteralPath '%CHECKSUM_FILE%' -Value ($h + '  Magneto_Electro_Optical_Transport_V3.exe') -Encoding ASCII; Write-Host ('SHA-256: ' + $h)"
if errorlevel 1 goto :failed
for %%F in ("%OUTPUT_EXE%") do echo Verified EXE size: %%~zF bytes

echo.
echo ============================================================
echo BUILD AND INSTALL COMPLETED SUCCESSFULLY
echo All final deliverables:
echo   %OUTPUT_DIR%
echo Verified one-file EXE:
echo   %OUTPUT_EXE%
echo Desktop shortcut:
echo   Magneto-Electro-Optical Transport V3
echo Offline instrument tests: PASS
echo PCO native DLL payload and load self-test: PASS
echo ============================================================
echo.
pause
exit /b 0

:failed
echo.
echo ============================================================
echo BUILD OR INSTALL FAILED
echo Read the first ERROR above and README_LAB_BUILD_CN.md.
echo Do not use a partially generated EXE for instrument control.
echo ============================================================
echo.
pause
exit /b 1

:locate_python
set "PYTHON_BASE="
for /f "usebackq delims=" %%P in (`py -3.11 -c "import sys; print(sys.executable)" 2^>nul`) do set "PYTHON_BASE=%%P"
if not defined PYTHON_BASE if exist "%~dp0python\python.exe" set "PYTHON_BASE=%~dp0python\python.exe"
if not defined PYTHON_BASE if exist "%~dp0..\python\python.exe" set "PYTHON_BASE=%~dp0..\python\python.exe"
if not defined PYTHON_BASE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYTHON_BASE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYTHON_BASE for /f "usebackq delims=" %%P in (`where python 2^>nul`) do (
    if not defined PYTHON_BASE (
        "%%P" -c "import sys,struct; assert sys.version_info[:2]==(3,11) and struct.calcsize('P')==8" >nul 2>&1
        if not errorlevel 1 set "PYTHON_BASE=%%P"
    )
)
if defined PYTHON_BASE (
    "!PYTHON_BASE!" -c "import sys,struct; assert sys.version_info[:2]==(3,11) and struct.calcsize('P')==8" >nul 2>&1
    if errorlevel 1 set "PYTHON_BASE="
)
if defined PYTHON_BASE exit /b 0
exit /b 1

:install_python
echo.
where winget >nul 2>&1
if not errorlevel 1 (
    echo Trying Windows Package Manager ^(winget^)...
    winget install --id Python.Python.3.11 -e --scope user --silent --accept-source-agreements --accept-package-agreements --disable-interactivity
    if not errorlevel 1 (
        call :locate_python
        if defined PYTHON_BASE (
            echo Python 3.11 installation completed through winget.
            exit /b 0
        )
    )
    echo WARNING: winget did not provide a usable 64-bit Python 3.11.
) else (
    echo Windows Package Manager is unavailable.
)

echo Falling back to the official Python Software Foundation installer...
echo Downloading Python !PYTHON_INSTALLER_VERSION! x64 from python.org...
if exist "!PYTHON_INSTALLER!" del /Q "!PYTHON_INSTALLER!"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Stop';" ^
    "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;" ^
    "Invoke-WebRequest -UseBasicParsing -Uri '!PYTHON_INSTALLER_URL!' -OutFile '!PYTHON_INSTALLER!';" ^
    "$signature=Get-AuthenticodeSignature -FilePath '!PYTHON_INSTALLER!';" ^
    "if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation'){throw ('Unexpected installer signature: ' + $signature.Status + ' / ' + $signature.SignerCertificate.Subject)};" ^
    "Write-Host ('Verified installer signature: ' + $signature.SignerCertificate.Subject)"
if errorlevel 1 (
    echo ERROR: Official Python installer download or signature verification failed.
    if exist "!PYTHON_INSTALLER!" del /Q "!PYTHON_INSTALLER!"
    exit /b 1
)

echo Installing Python !PYTHON_INSTALLER_VERSION! x64 for the current Windows user...
start "" /wait "!PYTHON_INSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
set "PYTHON_INSTALL_RESULT=!ERRORLEVEL!"
del /Q "!PYTHON_INSTALLER!" >nul 2>&1
if not "!PYTHON_INSTALL_RESULT!"=="0" (
    echo ERROR: Official Python installer returned exit code !PYTHON_INSTALL_RESULT!.
    exit /b 1
)
echo Official Python installation completed.
exit /b 0
