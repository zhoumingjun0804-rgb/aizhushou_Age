# Remote Deploy Extras Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one `./deploy.sh remote` run handle remote upload, base deploy, rembg enablement, and Playwright capability detection without any manual follow-up on the CentOS 7 test server.

**Architecture:** Keep the existing SSH + rsync + remote `deploy.sh` flow. Extend the remote deploy path so CentOS 7 still uses a slim base requirements file, then install compatible extras in a second phase: rembg with a pinned NumPy stack, plus Playwright package installation and an explicit OS capability check for browser runtime. The deploy script remains the single entrypoint.

**Tech Stack:** Bash, PM2, Python venv, pip, Miniconda Python fallback, sshpass/rsync, rembg, onnxruntime, Playwright.

---

### Task 1: Add deploy extras phase to `deploy.sh`

**Files:**
- Modify: `deploy.sh`

- [ ] **Step 1: Add small OS and module helper functions**

Add helpers near the existing environment/python helper section for:
- checking CentOS 7
- checking whether a Python module imports cleanly from `backend/.venv`
- printing consistent warnings for unsupported browser runtime

- [ ] **Step 2: Add a remote extras installer**

Implement a focused function that:
- runs only after venv creation
- installs `numpy>=1.24,<2`
- installs compatible rembg dependencies
- validates rembg import
- installs Playwright Python package
- skips Chromium installation on CentOS 7 with a clear warning about glibc

- [ ] **Step 3: Call the extras installer from the normal setup path**

Hook the new function into `cmd_setup()` so both local/remote deploy reuse the same setup sequence, but only CentOS 7 deploys actually take the slim-requirements + extras path.

- [ ] **Step 4: Update help text**

Clarify in `usage()` that `remote` now includes remote optional feature setup automatically.

### Task 2: Keep CentOS 7 package set compatible

**Files:**
- Modify: `backend/requirements-deploy.txt`
- Modify: `deploy.sh`

- [ ] **Step 1: Document deploy requirements intent**

Update `backend/requirements-deploy.txt` comments so it is clear the file is a base install only and feature extras are installed by script logic.

- [ ] **Step 2: Pin rembg-compatible package versions in script**

Use exact pip install commands in `deploy.sh` for the CentOS 7 extras phase, including:
- `numpy>=1.24,<2`
- `greenlet>=3.1.1,<4`
- `opencv-python-headless<4.10`
- `onnxruntime`
- `rembg`
- `playwright`

- [ ] **Step 3: Preserve success when browser runtime is impossible**

Ensure deploy does not fail just because Playwright browser install is unavailable on CentOS 7. Instead, print a warning and continue with the rest of deploy.

### Task 3: Verify one-command behavior end-to-end

**Files:**
- Modify: `deploy.sh` (if verification reveals issues)

- [ ] **Step 1: Syntax check**

Run:

```bash
bash -n ./deploy.sh
```

Expected: no output, exit code `0`.

- [ ] **Step 2: Run remote deploy**

Run:

```bash
cd /path/to/project
./deploy.sh remote
```

Expected:
- remote sync succeeds
- remote PM2 app becomes `online`
- remote health check passes on the configured `PORT`
- rembg install/import succeeds
- Playwright prints a CentOS 7 incompatibility warning instead of aborting deploy

- [ ] **Step 3: Verify rembg and service state remotely**

Run:

```bash
ssh deploy_user@your-server.example.com '<remote-app-dir>/backend/.venv/bin/python3 -c "from rembg import remove; print(\"rembg OK\")" && pm2 describe aizhushou-age | head -15'
```

Expected:
- `rembg OK`
- PM2 app status `online`

- [ ] **Step 4: Commit**

Do not commit unless explicitly requested by the user.
