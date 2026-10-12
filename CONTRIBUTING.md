# Contributing to Storage Auditor & Safe Mover

Thank you for your interest in contributing! This project is dedicated to providing high-performance, non-destructive file auditing and safe data migration on Windows.

---

## Core Safety Principles

Any contribution to this project must respect our non-negotiable architectural rules:
1. **Engine 1 (Auditor) is Strictly Read-Only:** It must never write, rename, move, or delete files on inspected drives.
2. **Engine 2 (Safe Mover) is Copy-Only by Default:** It validates source existence, stages copies in temporary target files (`.~tmp_safecopy_*.tmp`), verifies dual SHA-256 hashes, and preserves source files 100% intact.
3. **No Automatic Deletion:** Destructive operations (moving or deleting sources) require explicit user authorization.

---

## Development Workflow

1. **Fork and Clone:**
   ```powershell
   git clone https://github.com/your-username/storage-auditor.git
   cd storage-auditor
   ```

2. **Environment Setup:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. **Code Style:**
   - Adhere to **PEP 8** standards.
   - Use type annotations wherever applicable (`from __future__ import annotations`).
   - Keep modules decoupled (`core/`, `gui/`, `exporters/`).

4. **Testing:**
   Before submitting changes, all unit and integration tests must pass:
   ```powershell
   pytest -v
   ```

5. **Submitting a Pull Request:**
   - Create a feature branch (`git checkout -b feature/my-feature`).
   - Write clear, descriptive commit messages.
   - Ensure no local artifacts, logs, or personal paths are included.
   - Open a Pull Request detailing the problem solved and testing steps.
