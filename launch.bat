@echo off
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"
streamlit run app.py
pause
