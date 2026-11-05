@echo off
rem Double-click to build "don't know what im doing" for Windows (needs Python 3.12 from python.org).
powershell -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1"
pause
