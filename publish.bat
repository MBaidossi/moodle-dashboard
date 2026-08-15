@echo off
REM Regenerates the dashboard and publishes it to GitHub Pages.
REM Adjust the path below if you ever move this folder.

cd /d "C:\Users\Mohammad\Downloads\moodle-assignment-dashboard"

python3 moodle_dashboard.py

if not exist docs mkdir docs
copy /Y dashboard.html docs\index.html

git add docs\index.html
git commit -m "Auto-update dashboard"
git push

echo Done.