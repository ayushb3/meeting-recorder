# scripts/

Helper scripts for development and documentation tasks.

## demo_vault.py

Generates a throwaway vault populated with believable but entirely fictional
meeting notes. Its sole purpose is to allow documentation screenshots to be
taken without exposing real meeting names, colleague names, or internal project
names from the user's actual vault.

Usage:

```
python scripts/demo_vault.py [TARGET_DIR] [--force]
```

`TARGET_DIR` defaults to `~/Documents/MeetingRecorderDemo`. The script prints
step-by-step instructions for switching the app to the demo vault, taking
screenshots, and restoring the real vault afterwards.

**Safety:** the script refuses to run if the target directory already contains
files, unless `--force` is passed. This makes it impossible to accidentally
overwrite the real vault.

See `docs/screenshots/README.md` for the full screenshot workflow.
