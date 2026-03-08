# Installation Guide

> This hardened build defaults to safe behavior:
> - Private/local network targets are blocked
> - Browser sandbox stays enabled unless explicitly disabled

## 📦 Quick Installation

### 1. Clone or Download the Skill

```bash
# Method 1: Using git clone (if public repo)
git clone https://github.com/waisimon/playwright-scraper-skill.git
cd playwright-scraper-skill

# Method 2: Download ZIP and extract
# After downloading, enter the directory
cd playwright-scraper-skill
```

### 2. Install Dependencies

```bash
# Install once, then reuse with state tracking
bash scripts/ensure-runtime.sh
```

### 3. Test

```bash
# Quick test
node scripts/playwright-simple.js https://example.com

# Test Stealth version
node scripts/playwright-stealth.js https://example.com
```

---

## 🔧 Advanced Installation

### Using with OpenClaw

If you're using OpenClaw, you can place this skill in the skills directory:

```bash
# Assuming your OpenClaw workspace is at ~/.openclaw/workspace
cp -r playwright-scraper-skill ~/.openclaw/workspace/skills/

# Then you can invoke it in OpenClaw
```

---

## ✅ Verify Installation

Run the example script:

```bash
# Discuss.com.hk example (verified working)
bash examples/discuss-hk.sh
```

If you see output similar to this, installation is successful:

```
🕷️  Starting Playwright Stealth scraper...
📱 Navigating to: https://m.discuss.com.hk/#hot
📡 HTTP Status: 200
✅ Scraping complete!
```

Run full smoke tests:

```bash
bash test.sh
```

You should see `私網阻擋正常` / private network blocking pass.

---

## 🐛 Common Issues

### Issue: Playwright not found

**Error message:** `Error: Cannot find module 'playwright'`

**Solution:**
```bash
bash scripts/ensure-runtime.sh
```

### Issue: Browser launch failed

**Error message:** `browserType.launch: Executable doesn't exist`

**Solution:**
```bash
bash scripts/ensure-runtime.sh
```

### Issue: Dependencies reinstall on every run

**Solution:**
```bash
bash scripts/ensure-runtime.sh
cat .state/runtime-state.json
```

Use the state file to confirm last checks (`checkedAt`) and whether runtime was reused (`source`).

### Issue: Permission errors

**Error message:** `Permission denied`

**Solution:**
```bash
chmod +x scripts/*.js
chmod +x examples/*.sh
```

### Issue: Need private network scraping (trusted environment only)

**Solution:**
```bash
ALLOW_PRIVATE_NETWORK=true node scripts/playwright-simple.js http://10.0.0.8
```

### Issue: Browser sandbox incompatibility in container

**Solution (only if required):**
```bash
DISABLE_SANDBOX=true node scripts/playwright-stealth.js https://example.com
```

---

## 📝 System Requirements

- **Node.js:** v18+ recommended
- **OS:** macOS / Linux / Windows
- **Disk Space:** ~500MB (including Chromium)
- **RAM:** 2GB+ recommended

---

## 🚀 Next Steps

After installation, check out:
- [README.md](README.md) — Quick reference
- [SKILL.md](SKILL.md) — Full documentation
- [examples/](examples/) — Example scripts
