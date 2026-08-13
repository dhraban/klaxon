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

if (!outputPath || !playwrightPath || !Number.isInteger(targetCount)) {
  throw new Error("Expected output path, post count, and Playwright path.");
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

  for (let attempt = 0; attempt < 24; attempt += 1) {
    await page.waitForTimeout(attempt === 0 ? 5000 : 1600);
    const html = await page.content();
    const snapshotIds = new Set(
      Array.from(html.matchAll(/"post_id":"(\d+)"/g), (match) => match[1]),
    );
    const priorCount = observedPostIds.size;
    for (const postId of snapshotIds) observedPostIds.add(postId);

    if (snapshots.length === 0 || observedPostIds.size > priorCount) {
      snapshots.push(html);
    }
    if (observedPostIds.size >= targetCount) break;

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
    }),
  );
} finally {
  await browser.close();
}
