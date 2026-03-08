#!/bin/bash
# Security-focused smoke tests

set -euo pipefail

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "🧪 Playwright Scraper Skill 測試"
echo ""

# Ensure runtime once (idempotent)
bash scripts/ensure-runtime.sh
echo ""

# Test 1: Playwright Simple
echo "📝 測試 1: Playwright Simple (Example.com)"
node scripts/playwright-simple.js https://example.com > "$TMP_DIR/test-simple.json"
if grep -q "Example Domain" "$TMP_DIR/test-simple.json"; then
  echo "✅ Simple 模式正常"
else
  echo "❌ Simple 模式失敗"
  exit 1
fi
echo ""

# Test 2: Playwright Stealth
echo "📝 測試 2: Playwright Stealth (Example.com)"
node scripts/playwright-stealth.js https://example.com > "$TMP_DIR/test-stealth.json"
if grep -q "Example Domain" "$TMP_DIR/test-stealth.json"; then
  echo "✅ Stealth 模式正常"
else
  echo "❌ Stealth 模式失敗"
  exit 1
fi
echo ""

# Test 3: Environment variable
echo "📝 測試 3: 環境變數 (WAIT_TIME)"
WAIT_TIME=1000 node scripts/playwright-simple.js https://example.com > "$TMP_DIR/test-env.json"
if grep -q "Example Domain" "$TMP_DIR/test-env.json"; then
  echo "✅ 環境變數正常"
else
  echo "❌ 環境變數失敗"
  exit 1
fi
echo ""

# Test 4: Private network blocking (default secure behavior)
echo "📝 測試 4: 私網阻擋（預設）"
if node scripts/playwright-simple.js http://127.0.0.1 > "$TMP_DIR/test-private.log" 2>&1; then
  echo "❌ 私網阻擋失敗（應該拒絕）"
  exit 1
else
  echo "✅ 私網阻擋正常"
fi
echo ""

echo "✅ 所有測試通過！"
