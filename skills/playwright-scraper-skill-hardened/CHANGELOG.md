# Changelog

## [1.2.1] - 2026-03-04

### 🔐 Security Hardening

- Block private/local/metadata network targets by default (`ALLOW_PRIVATE_NETWORK=false`)
- Restrict target protocol to `http/https`
- Add request-level guard to stop unsafe subrequests
- Keep sandbox enabled by default (no default `--no-sandbox`)

### 🛠 Reliability Improvements

- Handle `page.goto()` null response safely
- Add `try/finally` browser cleanup in both scripts
- Add bounded env parsing (`WAIT_TIME`, `NAV_TIMEOUT`, output limits)

### ✅ Testing

- Update `test.sh` with private-network blocking regression test

## [1.2.0] - 2026-02-07

### 🔄 Major Changes

- **Project Renamed** — `web-scraper` → `playwright-scraper-skill`
- Updated all documentation and links
- Updated GitHub repo name
- **Bilingual Documentation** — All docs now in English (with Chinese README available)

---

## [1.1.0] - 2026-02-07

### ✅ Added

- **LICENSE** — MIT License
- **CONTRIBUTING.md** — Contribution guidelines
- **examples/README.md** — Detailed usage examples
- **test.sh** — Automated test script
- **README.md** — Redesigned with badges

### 🔧 Improvements

- Clearer file structure
- More detailed documentation
- More practical examples

---

## [1.0.0] - 2026-02-07

### ✅ Initial Release

**Tools Created:**
- ✅ `playwright-simple.js` — Fast simple scraper
- ✅ `playwright-stealth.js` — Anti-bot protected version (primary) ⭐

**Test Results:**
- ✅ Discuss.com.hk success (200 OK, 19.6s)
- ✅ Example.com success (3.4s)
- ✅ Auto fallback to deep-scraper's Playwright

**Documentation:**
- ✅ SKILL.md (full documentation)
- ✅ README.md (quick reference)
- ✅ Example scripts (discuss-hk.sh)
- ✅ package.json

**Key Findings:**
1. **Playwright Stealth is the best solution** (100% success on Discuss.com.hk)
2. **Don't use Crawlee** (easily detected)
3. **Chaser (Rust) doesn't work currently** (blocked by Cloudflare)
4. **Hiding `navigator.webdriver` is key**

---

## Future Plans

- [ ] Add proxy IP rotation
- [ ] CAPTCHA handling integration
- [ ] Cookie management (maintain login state)
- [ ] Batch scraping (parallel processing)
- [ ] Integration with OpenClaw browser tool
