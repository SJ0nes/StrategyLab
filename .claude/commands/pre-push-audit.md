---
description: Run the manual pre-push audit and report what it found
allowed-tools: PowerShell(powershell -NoProfile -ExecutionPolicy Bypass -File .claude/hooks/pre-push-audit.ps1), Read
---

Run the pre-push audit:

```
powershell -NoProfile -ExecutionPolicy Bypass -File .claude/hooks/pre-push-audit.ps1
```

Then read `state/audit/pre-push.txt` and report to the operator:

- the commits that would be pushed,
- the diff stat against the remote (or that there is no upstream, so everything is unpushed),
- every hit from the scan, quoted, with the file it came from.

Do not push. Pushing is a manual act the operator performs themselves; a public git history is
permanent. If the audit is not clean, say plainly that it is not clean and stop there.
