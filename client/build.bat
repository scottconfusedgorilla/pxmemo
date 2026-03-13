@echo off
echo Building pxmemo.exe...
pip install pyinstaller piexif requests 2>nul
python -m PyInstaller --onefile --noconsole --name pxmemo pxmemo.py
echo.
echo Done! Executable is in dist\pxmemo.exe
pause
