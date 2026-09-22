"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const {
  adaptChannelBrief,
  formatCount,
  formatDuration,
  formatRate,
} = require("./channel-adapter.js");

function examplesDir() {
  const docs = path.join(__dirname, "..", "docs");
  const sub = fs.readdirSync(docs).find((d) => {
    return d.startsWith("02-") && fs.statSync(path.join(docs, d)).isDirectory();
  });
  assert.ok(sub, "docs/02-* construction dir");
  return path.join(docs, sub, "examples");
}

function loadDemo() {
  const p = path.join(examplesDir(), "channel-brief-demo.json");
  assert.ok(fs.existsSync(p), "channel-brief-demo.json must exist");
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

function testFormatHelpers() {
  assert.strictEqual(formatCount(1280000), "128 万");
  assert.strictEqual(formatCount(240000000), "2.4 亿");
  assert.strictEqual(formatCount(null), "—");
  assert.strictEqual(formatDuration(979), "16:19");
  assert.strictEqual(formatDuration(null), "—");
  assert.strictEqual(formatRate(0.038), "3.8%");
}

function testAdaptDemoStructure() {
  const data = loadDemo();
  const view = adaptChannelBrief(data);
  assert.ok(view, "adapt returned null");
  assert.strictEqual(view.channel.title, "光圈实验室 ApertureLab");
  assert.ok(view.channel.handle.indexOf("@") === 0);
  assert.ok(view.health.statuses.length >= 2);
  assert.strictEqual(view.metrics.avgViewDisplay, "153 万");
  assert.strictEqual(view.metrics.avgDurationDisplay, "16:19");
  assert.strictEqual(view.recentVsHot.recent.videoCount, 3);
  assert.strictEqual(view.recentVsHot.hot.videoCount, 2);
  assert.ok(view.contentHabits.typeBars.length >= 1);
  assert.ok(view.contentHabits.theses.length === 5);
  assert.strictEqual(view.audienceHabits.pos, 64);
  assert.strictEqual(view.showCommercial, true);
  assert.ok(view.commercial);
  assert.ok(view.commercial.excerpts.length >= 1);
  assert.strictEqual(view.videos.length, 5);
  assert.strictEqual(view.ops.verdict, null);
  // Ban system cooperation / crisis copy in view model strings we surface as labels
  const blob = JSON.stringify(view);
  assert.ok(blob.indexOf("建议合作") < 0);
  assert.ok(blob.indexOf("适合合作") < 0);
  assert.ok(blob.indexOf("危机") < 0);
}

function testCommercialOmittedWhenMissing() {
  const data = loadDemo();
  delete data.commercial;
  const view = adaptChannelBrief(data);
  assert.strictEqual(view.showCommercial, false);
  assert.strictEqual(view.commercial, null);
}

function testCommercialOmittedWhenEmpty() {
  const data = loadDemo();
  data.commercial = {
    commercial_nonnull_rate: 0,
    ad_overlay_count: 0,
    cta_purchase_rate: 0,
    excerpts: [],
  };
  const view = adaptChannelBrief(data);
  assert.strictEqual(view.showCommercial, false);
  assert.strictEqual(view.commercial, null);
}

function testAdaptNull() {
  assert.strictEqual(adaptChannelBrief(null), null);
  assert.doesNotThrow(() => adaptChannelBrief({}));
  const empty = adaptChannelBrief({});
  assert.ok(empty);
  assert.strictEqual(empty.showCommercial, false);
  assert.deepStrictEqual(empty.videos, []);
}

function testChildTaskIdPassthrough() {
  const data = loadDemo();
  data.videos[0].child_task_id = "child-abc";
  data.videos[0].pipeline_status = "completed";
  const view = adaptChannelBrief(data);
  assert.strictEqual(view.videos[0].childTaskId, "child-abc");
  assert.strictEqual(view.videos[0].pending, false);
}

async function main() {
  const tests = [
    ["format helpers", testFormatHelpers],
    ["adapt demo structure", testAdaptDemoStructure],
    ["commercial omitted when missing", testCommercialOmittedWhenMissing],
    ["commercial omitted when empty", testCommercialOmittedWhenEmpty],
    ["adapt null / empty", testAdaptNull],
    ["child_task_id passthrough", testChildTaskIdPassthrough],
  ];
  for (const [name, fn] of tests) {
    await fn();
    console.log("ok", name);
  }
  console.log("all passed", tests.length);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
