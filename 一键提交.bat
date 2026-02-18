@echo off
setlocal

:: Title
title One-Click Git Push

:: Check if git is installed
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Git is not installed.
    pause
    exit /b
)

:: Check if user input origin
echo [INFO] Please confirm remote repository is set.
echo [INFO] Current Remotes:
git remote -v
echo.

if not exist ".git" (
    echo [INFO] Initializing git repository...
    git init
    echo [INFO] Please add remote repository URL (e.g. https://github.com/user/repo)
    set /p remote_url=Remote URL:
    if not "%remote_url%"=="" git remote add origin %remote_url%
)

:: Add all files
echo [INFO] Adding files...
git add .

:: Commit
set /p commit_msg=Commit Message (Press Enter for "Update"):
if "%commit_msg%"=="" set commit_msg=Update

git commit -m "%commit_msg%"

:: Push
echo [INFO] Pushing to origin...
git push -u origin main
if %errorlevel% neq 0 (
    echo [WARN] Push failed. Trying master branch...
    git push -u origin master
)

echo [INFO] Done.
pause
