@echo off
cd /d c:\Users\krivo\trading-bot-v2
python scripts\label_outcomes.py >> logs\journal_update.log 2>&1
python scripts\build_journal.py >> logs\journal_update.log 2>&1
