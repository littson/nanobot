#!/usr/bin/env node

const dns = require("dns").promises;
const net = require("net");
const fs = require("fs");
const path = require("path");

const METADATA_HOSTS = new Set([
  "metadata",
  "metadata.google.internal",
  "metadata.google.internal.",
  "metadata.azure.internal",
  "instance-data",
]);

function parseBooleanEnv(value, defaultValue = false) {
  if (value === undefined) return defaultValue;
  const normalized = String(value).trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  return defaultValue;
}

function parseIntEnv(name, defaultValue, min, max) {
  const raw = process.env[name];
  const parsed = raw === undefined ? defaultValue : Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed)) {
    throw new Error(`Invalid ${name}: ${raw}`);
  }
  return Math.max(min, Math.min(max, parsed));
}

function normalizeHostname(hostname) {
  return String(hostname || "").trim().toLowerCase().replace(/\.$/, "");
}

function isPrivateIPv4(ip) {
  const parts = ip.split(".").map((part) => Number.parseInt(part, 10));
  if (parts.length !== 4 || parts.some((n) => Number.isNaN(n) || n < 0 || n > 255)) {
    return true;
  }

  const [a, b] = parts;

  if (a === 0) return true;
  if (a === 10) return true;
  if (a === 127) return true;
  if (a === 169 && b === 254) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  if (a === 100 && b >= 64 && b <= 127) return true;
  if (a === 198 && (b === 18 || b === 19)) return true;
  if (a >= 224) return true;

  return false;
}

function isPrivateIPv6(ip) {
  const normalized = ip.toLowerCase();
  if (normalized === "::" || normalized === "::1") return true;
  if (normalized.startsWith("fc") || normalized.startsWith("fd")) return true;
  if (normalized.startsWith("fe8") || normalized.startsWith("fe9")) return true;
  if (normalized.startsWith("fea") || normalized.startsWith("feb")) return true;
  return false;
}

function isLocalOrMetadataHost(hostname) {
  const host = normalizeHostname(hostname);
  if (!host) return true;
  if (host === "localhost" || host.endsWith(".localhost")) return true;
  if (host.endsWith(".local")) return true;
  if (METADATA_HOSTS.has(host)) return true;
  return false;
}

function isPrivateAddress(address) {
  const version = net.isIP(address);
  if (version === 4) return isPrivateIPv4(address);
  if (version === 6) return isPrivateIPv6(address);
  return false;
}

async function hostResolvesToPrivate(hostname, cache) {
  const host = normalizeHostname(hostname);
  if (cache.has(host)) return cache.get(host);
  if (isLocalOrMetadataHost(host)) {
    cache.set(host, true);
    return true;
  }

  if (net.isIP(host)) {
    const direct = isPrivateAddress(host);
    cache.set(host, direct);
    return direct;
  }

  let addresses;
  try {
    addresses = await dns.lookup(host, { all: true, verbatim: true });
  } catch (error) {
    throw new Error(`DNS lookup failed for ${host}: ${error.message}`);
  }
  if (!Array.isArray(addresses) || addresses.length === 0) {
    throw new Error(`DNS lookup returned no addresses for ${host}`);
  }

  const privateHit = addresses.some((entry) => isPrivateAddress(entry.address));
  cache.set(host, privateHit);
  return privateHit;
}

function parseUrlOrThrow(rawUrl) {
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    throw new Error(`Invalid URL: ${rawUrl}`);
  }

  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error(`Unsupported protocol: ${parsed.protocol}. Only http/https are allowed.`);
  }
  return parsed;
}

async function assertSafeTarget(rawUrl, options = {}) {
  const allowPrivateNetwork = Boolean(options.allowPrivateNetwork);
  const dnsCache = options.dnsCache || new Map();
  const parsed = parseUrlOrThrow(rawUrl);

  if (!allowPrivateNetwork) {
    const blocked = await hostResolvesToPrivate(parsed.hostname, dnsCache);
    if (blocked) {
      throw new Error(
        `Blocked target host: ${parsed.hostname}. Private/local/metadata network access is disabled by default.`
      );
    }
  }

  return parsed;
}

function shouldCheckRequestUrl(rawUrl) {
  try {
    const parsed = new URL(rawUrl);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

async function installRequestGuards(context, options = {}) {
  const allowPrivateNetwork = Boolean(options.allowPrivateNetwork);
  const dnsCache = options.dnsCache || new Map();
  if (allowPrivateNetwork) return;

  await context.route("**/*", async (route) => {
    const requestUrl = route.request().url();
    if (!shouldCheckRequestUrl(requestUrl)) {
      await route.continue();
      return;
    }

    try {
      await assertSafeTarget(requestUrl, { allowPrivateNetwork: false, dnsCache });
      await route.continue();
    } catch {
      await route.abort("blockedbyclient");
    }
  });
}

function ensureParentDir(filePath) {
  const absolutePath = path.resolve(filePath);
  const dir = path.dirname(absolutePath);
  fs.mkdirSync(dir, { recursive: true });
  return absolutePath;
}

function buildLaunchArgs(disableSandbox) {
  if (!disableSandbox) return [];
  return ["--no-sandbox", "--disable-setuid-sandbox"];
}

module.exports = {
  assertSafeTarget,
  buildLaunchArgs,
  ensureParentDir,
  installRequestGuards,
  parseBooleanEnv,
  parseIntEnv,
};
