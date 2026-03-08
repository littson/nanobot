---
name: playwright-scraper-skill
description: Playwright-based web scraping OpenClaw Skill with anti-bot protection. Successfully tested on complex sites like Discuss.com.hk.
version: 1.2.1
author: Simon Chan
metadata: {"nanobot":{"requires":{"bins":["node","npm","npx"]}}}
---

# Playwright Scraper Skill

A Playwright-based web scraping OpenClaw Skill with anti-bot protection. Includes hardened defaults to reduce SSRF/local-network risk.

Runtime model:
- Node.js + npm + Playwright (this skill is **Node-only**)
- Do **not** use `pip install playwright` for this skill
- Use `bash scripts/ensure-runtime.sh` to install once and reuse with state tracking

---

## 🎯 Use Case Matrix

| Target Website | Anti-Bot Level | Recommended Method | Script |
|---------------|----------------|-------------------|--------|
| **Regular Sites** | Low | web_fetch tool | N/A (built-in) |
| **Dynamic Sites** | Medium | Playwright Simple | `scripts/playwright-simple.js` |
| **Cloudflare Protected** | High | **Playwright Stealth** ⭐ | `scripts/playwright-stealth.js` |
| **YouTube** | Special | deep-scraper | Install separately |
| **Reddit** | Special | reddit-scraper | Install separately |

---

## 📦 Installation

```bash
cd playwright-scraper-skill
bash scripts/ensure-runtime.sh
```

The runtime status is persisted in:

```text
.state/runtime-state.json
```

The state file records install/check timestamps, lockfile hash, Node/npm versions, and active Python `venv` context (if present in environment).

---

## 🚀 Quick Start

### 1️⃣ Simple Sites (No Anti-Bot)

Use OpenClaw's built-in `web_fetch` tool:

```bash
# Invoke directly in OpenClaw
Hey, fetch me the content from https://example.com
```

---

### 2️⃣ Dynamic Sites (Requires JavaScript)

Use **Playwright Simple**:

```bash
bash scripts/ensure-runtime.sh
node scripts/playwright-simple.js "https://example.com"
```

**Example output:**
```json
{
  "url": "https://example.com",
  "title": "Example Domain",
  "content": "...",
  "elapsedSeconds": "3.45"
}
```

---

### 3️⃣ Anti-Bot Protected Sites (Cloudflare etc.)

Use **Playwright Stealth**:

```bash
bash scripts/ensure-runtime.sh
node scripts/playwright-stealth.js "https://m.discuss.com.hk/#hot"
```

**Features:**
- Hide automation markers (`navigator.webdriver = false`)
- Realistic User-Agent (iPhone, Android)
- Random delays to mimic human behavior
- Screenshot and HTML saving support

---

### 4️⃣ YouTube Video Transcripts

Use **deep-scraper** (install separately):

```bash
# Install deep-scraper skill
npx clawhub install deep-scraper

# Use it
cd skills/deep-scraper
node assets/youtube_handler.js "https://www.youtube.com/watch?v=VIDEO_ID"
```

---

## 📖 Script Descriptions

### `scripts/playwright-simple.js`
- **Use Case:** Regular dynamic websites
- **Speed:** Fast (3-5 seconds)
- **Anti-Bot:** None
- **Output:** JSON (title, content, URL)

### `scripts/playwright-stealth.js` ⭐
- **Use Case:** Sites with Cloudflare or anti-bot protection
- **Speed:** Medium (5-20 seconds)
- **Anti-Bot:** Medium-High (hides automation, realistic UA)
- **Output:** JSON + Screenshot + HTML file
- **Verified:** 100% success on Discuss.com.hk

---

## 🎓 Best Practices

### 1. Try web_fetch First
If the site doesn't have dynamic loading, use OpenClaw's `web_fetch` tool—it's fastest.

### 2. Need JavaScript? Use Playwright Simple
If you need to wait for JavaScript rendering, use `playwright-simple.js`.

### 3. Getting Blocked? Use Stealth
If you encounter 403 or Cloudflare challenges, use `playwright-stealth.js`.

### 4. Special Sites Need Specialized Skills
- YouTube → deep-scraper
- Reddit → reddit-scraper
- Twitter → bird skill

---

## 🔧 Customization

All scripts support environment variables:

```bash
# Set screenshot path
SCREENSHOT_PATH=/path/to/screenshot.png node scripts/playwright-stealth.js URL

# Set wait time (milliseconds)
WAIT_TIME=10000 node scripts/playwright-simple.js URL

# Navigation timeout (milliseconds)
NAV_TIMEOUT=45000 node scripts/playwright-simple.js URL

# Enable headful mode (show browser)
HEADLESS=false node scripts/playwright-stealth.js URL

# Allow private/local targets (default: blocked)
ALLOW_PRIVATE_NETWORK=true node scripts/playwright-simple.js http://10.0.0.8

# Disable browser sandbox only when required
DISABLE_SANDBOX=true node scripts/playwright-stealth.js URL

# Save HTML
SAVE_HTML=true node scripts/playwright-stealth.js URL

# Custom User-Agent
USER_AGENT="Mozilla/5.0 ..." node scripts/playwright-stealth.js URL
```

---

## 📊 Performance Comparison

| Method | Speed | Anti-Bot | Success Rate (Discuss.com.hk) |
|--------|-------|----------|-------------------------------|
| web_fetch | ⚡ Fastest | ❌ None | 0% |
| Playwright Simple | 🚀 Fast | ⚠️ Low | 20% |
| **Playwright Stealth** | ⏱️ Medium | ✅ Medium | **100%** ✅ |
| Puppeteer Stealth | ⏱️ Medium | ✅ Medium-High | ~80% |
| Crawlee (deep-scraper) | 🐢 Slow | ❌ Detected | 0% |
| Chaser (Rust) | ⏱️ Medium | ❌ Detected | 0% |

---

## 🛡️ Anti-Bot Techniques Summary

Lessons learned from our testing:

### ✅ Effective Anti-Bot Measures
1. **Hide `navigator.webdriver`** — Essential
2. **Realistic User-Agent** — Use real devices (iPhone, Android)
3. **Mimic Human Behavior** — Random delays, scrolling
4. **Avoid Framework Signatures** — Crawlee, Selenium are easily detected
5. **Use `addInitScript` (Playwright)** — Inject before page load

### ❌ Ineffective Anti-Bot Measures
1. **Only changing User-Agent** — Not enough
2. **Using high-level frameworks (Crawlee)** — More easily detected
3. **Docker isolation** — Doesn't help with Cloudflare

---

## 🔍 Troubleshooting

### Issue: 403 Forbidden
**Solution:** Use `playwright-stealth.js`

### Issue: Cloudflare Challenge Page
**Solution:**
1. Increase wait time (10-15 seconds)
2. Try `headless: false` (headful mode sometimes has higher success rate)
3. Consider using proxy IPs

### Issue: Blank Page
**Solution:**
1. Increase `waitForTimeout`
2. Use `waitUntil: 'networkidle'` or `'domcontentloaded'`
3. Check if login is required

### Issue: Target blocked as private/local
**Solution:**
1. This is expected secure behavior
2. Use `ALLOW_PRIVATE_NETWORK=true` only in trusted environments
3. Never expose this override to untrusted user input

### Issue: Runtime dependencies reinstall every run
**Solution:**
1. Always use `bash scripts/ensure-runtime.sh` (idempotent)
2. Check `.state/runtime-state.json` for `source` and `checkedAt`
3. If `package-lock.json` changed, reinstall is expected

### Issue: Agent runs `pip install playwright`
**Solution:**
1. This skill is Node-only; do not use Python Playwright commands
2. Use `npm run stealth-ready -- "<URL>"` or `npm run simple-ready -- "<URL>"`

---

## 📝 Memory & Experience

### 2026-02-07 Discuss.com.hk Test Conclusions
- ✅ **Pure Playwright + Stealth** succeeded (5s, 200 OK)
- ❌ Crawlee (deep-scraper) failed (403)
- ❌ Chaser (Rust) failed (Cloudflare)
- ❌ Puppeteer standard failed (403)

**Best Solution:** Pure Playwright + anti-bot techniques (framework-independent)

---

## 🚧 Future Improvements

- [ ] Add proxy IP rotation
- [ ] Implement cookie management (maintain login state)
- [ ] Add CAPTCHA handling (2captcha / Anti-Captcha)
- [ ] Batch scraping (parallel URLs)
- [ ] Integration with OpenClaw's `browser` tool

---

## 📚 References

- [Playwright Official Docs](https://playwright.dev/)
- [puppeteer-extra-plugin-stealth](https://github.com/berstend/puppeteer-extra/tree/master/packages/puppeteer-extra-plugin-stealth)
- [deep-scraper skill](https://clawhub.com/opsun/deep-scraper)
