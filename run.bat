@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
start "" /min cmd /c "python app.py"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5000/"
echo Server is running. Keep this window open while using the app.
pause
