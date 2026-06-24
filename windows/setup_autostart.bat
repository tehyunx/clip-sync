@echo off
REM ClipSync 시작프로그램 등록 스크립트
REM 관리자 권한 불필요 — 현재 사용자 작업 스케줄러에 등록

set SCRIPT_DIR=%~dp0
set PYTHON_SCRIPT=%SCRIPT_DIR%clipboard_sync.py

REM pip 패키지 설치
pip install websockets

REM 작업 스케줄러 등록 (로그온 시 자동 실행, 백그라운드)
schtasks /create /tn "ClipSync" /tr "pythonw \"%PYTHON_SCRIPT%\"" /sc onlogon /rl limited /f

echo.
echo [ClipSync] 시작프로그램 등록 완료.
echo 다음 로그온 시 자동으로 실행됩니다.
echo 지금 바로 실행하려면: pythonw "%PYTHON_SCRIPT%"
pause
