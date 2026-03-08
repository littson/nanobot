#!/usr/bin/env node
/**
 * Playwright Simple Scraper
 * 適用：一般動態網站，無反爬保護
 * 速度：快（3-5 秒）
 * 
 * Usage: node playwright-simple.js <URL>
 */

const { chromium } = require("playwright");
const {
  assertSafeTarget,
  buildLaunchArgs,
  ensureParentDir,
  installRequestGuards,
  parseBooleanEnv,
  parseIntEnv,
} = require("./security-utils");

const rawUrl = process.argv[2];
if (!rawUrl) {
  console.error("❌ 請提供 URL");
  console.error("用法: node playwright-simple.js <URL>");
  process.exit(1);
}

function loadConfig() {
  return {
    waitTime: parseIntEnv("WAIT_TIME", 3000, 0, 120000),
    navTimeout: parseIntEnv("NAV_TIMEOUT", 30000, 1000, 120000),
    contentMaxChars: parseIntEnv("CONTENT_MAX_CHARS", 5000, 200, 100000),
    allowPrivateNetwork: parseBooleanEnv(process.env.ALLOW_PRIVATE_NETWORK, false),
    disableSandbox: parseBooleanEnv(process.env.DISABLE_SANDBOX, false),
    headless: parseBooleanEnv(process.env.HEADLESS, true),
    screenshotPath: process.env.SCREENSHOT_PATH,
  };
}

async function main() {
  const {
    waitTime,
    navTimeout,
    contentMaxChars,
    allowPrivateNetwork,
    disableSandbox,
    headless,
    screenshotPath,
  } = loadConfig();

  console.log("🚀 啟動 Playwright 簡單版爬蟲...");
  const startTime = Date.now();
  const dnsCache = new Map();
  const safeTarget = await assertSafeTarget(rawUrl, { allowPrivateNetwork, dnsCache });

  let browser;
  try {
    browser = await chromium.launch({
      headless,
      args: buildLaunchArgs(disableSandbox),
    });

    const context = await browser.newContext();
    await installRequestGuards(context, { allowPrivateNetwork, dnsCache });
    const page = await context.newPage();

    console.log(`📱 導航到: ${safeTarget.toString()}`);
    const response = await page.goto(safeTarget.toString(), {
      waitUntil: "domcontentloaded",
      timeout: navTimeout,
    });
    const status = response ? response.status() : null;
    if (status !== null) {
      console.log(`📡 HTTP Status: ${status}`);
    } else {
      console.log("⚠️  無法取得 HTTP Status（可能是特殊跳轉）。");
    }

    console.log(`⏳ 等待 ${waitTime}ms...`);
    await page.waitForTimeout(waitTime);

    const result = await page.evaluate((maxChars) => {
      const bodyText = document.body?.innerText || "";
      return {
        title: document.title || "",
        url: window.location.href,
        content: bodyText.substring(0, maxChars),
        metaDescription: document.querySelector('meta[name="description"]')?.content || "",
      };
    }, contentMaxChars);
    result.httpStatus = status;

    if (screenshotPath) {
      const safePath = ensureParentDir(screenshotPath);
      await page.screenshot({ path: safePath });
      console.log(`📸 截圖已儲存: ${safePath}`);
      result.screenshot = safePath;
    }

    const elapsed = ((Date.now() - startTime) / 1000).toFixed(2);
    result.elapsedSeconds = elapsed;

    console.log("\n✅ 爬取完成！");
    console.log(JSON.stringify(result, null, 2));
  } finally {
    if (browser) {
      await browser.close();
    }
  }
}

main().catch((error) => {
  console.error(`❌ 爬取失敗: ${error.message}`);
  process.exit(1);
});
