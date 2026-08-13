#!/usr/bin/env node

import fs from "node:fs";
import { createRequire } from "node:module";


const outputPath = process.argv[2];
const targetCount = Number.parseInt(process.argv[3] || "5", 10);
const playwrightPath = process.argv[4];
const chromePath =
  process.argv[5] ||
  (process.platform === "darwin"
    ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    : "/usr/bin/google-chrome");
const pageUrl =
  process.argv[6] || "https://www.facebook.com/BOHECO1officialpage/posts";
const maxAttempts = Number.parseInt(process.argv[7] || "24", 10);
const stopPostIds = new Set(
  (process.argv[8] || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),
);

if (
  !outputPath ||
  !playwrightPath ||
  !Number.isInteger(targetCount) ||
  !Number.isInteger(maxAttempts)
) {
  throw new Error(
    "Expected output path, post count, Playwright path, and scroll-attempt count.",
  );
}

const require = createRequire(import.meta.url);
const { chromium } = require(playwrightPath);
const browser = await chromium.launch({
  executablePath: chromePath,
  headless: true,
  args: ["--incognito", "--no-first-run", "--disable-sync"],
});

try {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 1000 },
  });
  const page = await context.newPage();
  await page.goto(pageUrl, { waitUntil: "domcontentloaded", timeout: 45000 });

  const snapshots = [];
  const observedPostIds = new Set();
  let encounteredStopId = null;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    await page.waitForTimeout(attempt === 0 ? 5000 : 1600);
    const html = await page.content();
    const snapshotIds = new Set(
      Array.from(html.matchAll(/"post_id":"(\d+)"/g), (match) => match[1]),
    );
    const priorCount = observedPostIds.size;
    for (const postId of snapshotIds) observedPostIds.add(postId);
    encounteredStopId =
      Array.from(snapshotIds).find((postId) => stopPostIds.has(postId)) || null;

    if (snapshots.length === 0 || observedPostIds.size > priorCount) {
      snapshots.push(html);
    }
    if (encounteredStopId || observedPostIds.size >= targetCount) break;

    await page.evaluate(() => {
      window.scrollBy(0, Math.max(window.innerHeight * 0.85, 750));
    });
  }

  fs.writeFileSync(
    outputPath,
    snapshots.join("\n<!-- KLAXON FACEBOOK SNAPSHOT -->\n"),
    "utf8",
  );
  process.stdout.write(
    JSON.stringify({
      snapshotCount: snapshots.length,
      uniquePostCount: observedPostIds.size,
      encounteredStopId,
    }),
  );
} finally {
  await browser.close();
}
