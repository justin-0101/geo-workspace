# Windows clean-machine deployment

This is a **local, loopback-only** workspace for Windows 10/11 and CPython 3.11. It is not a LAN, multi-user, reverse-proxy, or public deployment.

## Fresh install

From the repository root in PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
# or, after creating the venv: .\install.ps1 -CheckOnly
.\.venv\Scripts\python.exe -m unittest discover -s . -p "test_*.py"
.\.venv\Scripts\python.exe start.py
.\.venv\Scripts\python.exe start.py --status
.\.venv\Scripts\python.exe start.py --stop
```

`install.ps1` creates `.venv`, installs `requirements.txt`, checks imports, required engine files, a writable data directory, free loopback ports, and Chrome/Chromium. Set `GEO_CHROME_PATH` when Chrome is installed outside the standard locations. `-SkipChrome` is only for API/UI checks; diagnosis and browser publishing still require Chrome.

To build a clean package from the current worktree instead of copying the whole checkout, run:

```powershell
.\package_release.ps1
# output: .\dist\geo-workspace\
```

The packager copies tracked source plus the required untracked runtime files, writes `RELEASE-MANIFEST.txt`, and excludes `data/`, backups, test results, virtual environments, logs, and browser state. Review the manifest before transferring the package.

The default ports are frontend `127.0.0.1:4173` and API `127.0.0.1:8798`. Set `GEO_FRONTEND_PORT` and `GEO_API_PORT` before starting both services to move them; they must be different, and generated `geo-config.js` keeps the browser UI aligned. Only IPv4 loopback (`127.0.0.1` or `localhost`) is supported; `::1` is rejected explicitly. Never set `GEO_BIND_HOST` to `0.0.0.0` or expose the API through a tunnel/proxy.

The diagnosis engine is the complete `skill/geo-diagnosis-single/` subtree, not only its Python entry point. The deliverable must also include `cover_gen.py` (used by publishing), `browser_paths.py`, `runtime_config.py`, `process_identity.py`, `start.py`, `stop.py`, `fonts/` and their OFL notices, and all tracked application files. `check_install.py` verifies this release-path manifest. Do not package `data/`, `backups/`, `test-results/`, `.venv/`, logs, browser profiles, or secrets.

## Fresh install versus migration

- **Fresh install:** leave `GEO_REDESIGN_DATA` unset (or point it at a new empty directory). The API creates and migrates its SQLite database on first start.
- **Data migration:** stop the old service, make an owner-approved backup of the old `data/` directory, set `GEO_REDESIGN_DATA` to that copied directory, run `check_install.py`, then start once and review the logs. The application performs additive startup migrations; migration success must be verified before deleting the source copy.
- Never casually copy `data/`, `backups/`, `data/browser-profile/`, SQLite files, evidence, uploaded originals, cookies, session state, API keys, AppSecret, or `.env` files between machines or users. These contain private material and may be tied to the original Windows account. Do not copy a live profile while a browser is running.
- Credentials are stored locally for this single-user design and are masked by API responses, but the current implementation does not provide OS-level encryption. Protect the checkout/data directory with Windows ACLs and treat backups as sensitive.

## Checks and boundaries

Use `check_install.py --data-dir <empty-check-directory> --json` for a machine-readable preflight. Root unit tests are deterministic and do not submit real platform questions. Browser/live checks require installed Chrome, login state, and network access; they are not a substitute for the install preflight. `python start.py --status` reports an unrelated process occupying either port instead of treating it as a healthy GEO service. `start.py` records each child command line and creation marker; `stop.py` terminates a PID only when both still match, and leaves it alone when Windows permissions prevent identity verification.

The current repository license status is documented in `LICENSE`, `LICENSE.zh.md`, and the README (PolyForm Noncommercial 1.0.0/source-available terms, not OSI-approved). This deployment work makes no license change; the owner must make the final legal/license decision.
