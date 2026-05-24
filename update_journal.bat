@echo off
cd /d c:\Users\krivo\trading-bot-v2
python scripts\analysis\label_outcomes.py >> logs\journal_update.log 2>&1
python scripts\analysis\label_main_ws.py >> logs\journal_update.log 2>&1
python scripts\analysis\bb_fade_label_outcomes.py >> logs\journal_update.log 2>&1
python scripts\build_journal.py >> logs\journal_update.log 2>&1
python scripts\cleanup_logs.py >> logs\journal_update.log 2>&1
