@echo off
setlocal
cd /d "%~dp0"
set PYTHON_EXE=C:\Users\Martin\AppData\Local\Programs\Python\Python311\python.exe
set PYTHONDONTWRITEBYTECODE=1
"%PYTHON_EXE%" -m resumen_escolar
