#!/usr/bin/env python3
"""Install one user LaunchAgent; refuse to overwrite an existing one."""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
target = Path.home() / "Library/LaunchAgents/com.maodou.job-search-digest.plist"
if target.exists():
    sys.exit("Schedule already exists; not overwriting " + str(target))
target.parent.mkdir(parents=True, exist_ok=True)
payload = {"Label": "com.maodou.job-search-digest",
           "ProgramArguments": [sys.executable, str(root / "digest.py")],
           "StartCalendarInterval": {"Hour": 9, "Minute": 0},
           "StandardOutPath": str(root / "digest.log"),
           "StandardErrorPath": str(root / "digest-error.log")}
with target.open("xb") as f:
    plistlib.dump(payload, f)
subprocess.run(["launchctl", "bootstrap", "gui/" + str(os.getuid()), str(target)], check=True)
print("Scheduled for 9:00 AM in the Mac's local time zone:", target)
