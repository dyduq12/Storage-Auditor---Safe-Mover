# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.3.x   | :white_check_mark: |
| < 0.3.0 | :x:                |

---

## Data Safety & Privacy Notice

**Storage Auditor & Safe Mover** interacts directly with file systems and disk metadata.

- **Engine 1** operates strictly read-only.
- **Engine 2** performs copy-only operations by default, verifying SHA-256 hashes before committing files.
- **Privacy Caution:** Execution logs (`.jsonl`), audit reports, and SQLite cache databases contain local file paths and filenames. **Never share raw report files or execution logs in public GitHub issues.**

---

## Reporting a Vulnerability

If you discover a security vulnerability or a bug that could lead to unintended data loss or corruption:

1. **Do not create a public issue.**
2. Send a detailed report describing:
   - The steps required to reproduce the behavior.
   - Operating system version and filesystem type (e.g., NTFS).
   - Expected vs. actual behavior.
3. Reports will be investigated promptly, and fixes will be prioritized before public release.
