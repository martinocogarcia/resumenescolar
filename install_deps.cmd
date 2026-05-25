@echo off
setlocal
cd /d "%~dp0"
set PYTHON_EXE=C:\Users\Martin\AppData\Local\Programs\Python\Python311\python.exe
set PYTHONDONTWRITEBYTECODE=1
"%PYTHON_EXE%" -m pip install --target .runtime\site-packages -r requirements.txt
if errorlevel 1 (
  echo.
  echo No se pudieron instalar las dependencias. Revisa VPN corporativa y acceso a ArtifactHub.
  exit /b 1
)
echo.
echo Dependencias instaladas.
