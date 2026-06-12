@echo off
REM Daily research-scout forward-logger (keyless, read-only, no keys/money/prod).
REM Appends one row to logs\scout\forward_series.csv (idempotent per day) + refreshes bundle.
REM Enable unattended daily run (Windows Task Scheduler), e.g.:
REM   schtasks /Create /TN "ScoutDaily" /TR "<repo>\bat\run_scout_daily.bat" /SC DAILY /ST 09:00
cd /d "%~dp0\.."
python src\scout\research_scout_orchestrator.py
