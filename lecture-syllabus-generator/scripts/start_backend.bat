@echo off
setlocal
cd /d "%~dp0..\backend"
if errorlevel 1 goto directory_error

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"

"%PYTHON_EXE%" -c "import uvicorn, fastapi, sqlalchemy, pydantic_settings, dotenv, multipart, yaml, docx, httpx, PIL" >nul 2>&1
if errorlevel 1 goto dependency_error

echo Python: %PYTHON_EXE%
echo Starting backend. After startup, open http://127.0.0.1:8000/
echo Press Ctrl+C to stop the server.
"%PYTHON_EXE%" run.py
set "BACKEND_EXIT_CODE=%ERRORLEVEL%"
if "%BACKEND_EXIT_CODE%"=="0" exit /b 0
echo.
echo [ERROR] Backend exited with code %BACKEND_EXIT_CODE%. See the error above.
pause
exit /b %BACKEND_EXIT_CODE%

:dependency_error
echo [ERROR] Python or backend dependencies are missing.
echo Selected Python: %PYTHON_EXE%
echo Run these commands from the backend folder:
echo   python -m venv .venv
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
pause
exit /b 1

:directory_error
echo [ERROR] Cannot find the backend folder next to scripts.
pause
exit /b 1
