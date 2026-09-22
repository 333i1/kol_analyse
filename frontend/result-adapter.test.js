"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

require("./result-adapter.js");
const adapt = globalThis.adaptAnalysisResult;

const LEVELS = new Set(["ok", "warn", "bad"]);

function examplesDir() {
  const docs = path.join(__dirname, "..", "docs");
  const sub = fs.readdirSync(docs).find((d) => {
    return d.startsWith("02-") && fs.statSync(path.join(docs, d)).isDirectory();
  });
  assert.ok(sub, "docs/02-* construction dir");
  return path.join(docs, sub, "examples");
}

function loadFixtures() {
  const dir = examplesDir();
  const files = fs.readdirSync(dir)
    .filter((f) => f.endsWith(".json") && !f.startsWith("channel-"))
    .sort();
  assert.strictEqual(files.length, 3, "expected three example fixtures, got " + files.join(","));
  return files.map((f) => ({
    name: f,
    data: JSON.parse(fs.readFileSync(path.join(dir, f), "utf8")),
  }));
}

function testAdaptThreeFixtures() {
  const fixtures = loadFixtures();
  for (const { name, data } of fixtures) {
    const out = adapt(data);
    assert.ok(out, name + ": adapt() returned null");
    assert.strictEqual(out.segs.length, 3, name + ": expected 3 segs");
    const sentKeys = Object.keys(out.sentiment).sort();
    assert.deepStrictEqual(sentKeys, ["neg", "neu", "pos"], name + ": sentiment three keys");
    assert.equal(typeof out.sentiment.pos, "number");
    assert.equal(typeof out.sentiment.neu, "number");
    assert.equal(typeof out.sentiment.neg, "number");
    for (const [k, v] of Object.entries(out.health)) {
      if (v && typeof v === "object" && "level" in v) {
        assert.ok(
          LEVELS.has(v.level),
          name + ": health." + k + ".level=" + v.level + " not in {ok,warn,bad}"
        );
      }
    }
    if (data.health && data.health.source && data.health.source.level === "degraded") {
      assert.strictEqual(out.health.source.level, "warn", name + ": degraded maps to warn");
    }
    if (data.health && data.health.source && data.health.source.level === "failed") {
      assert.strictEqual(out.health.source.level, "bad", name + ": failed maps to bad");
    }
  }
}

function testAdaptNullNoTypeError() {
  let out;
  assert.doesNotThrow(() => {
    out = adapt(null);
  }, "adapt(null) must not throw TypeError");
  assert.strictEqual(out, null);
}

function testCacheHitMissingResultNoTypeError() {
  const body = { status: "cache_hit", cache_hit: true };
  assert.doesNotThrow(() => {
    const adapted = adapt(body.result);
    if (!adapted) return;
    adapted.canonicalUrl = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
  }, "cache_hit missing result must not TypeError");
}


function testEmptySegsSkipSttNoThrow() {
  const result = {
    video: { video_id: "dQw4w9WgXcQ", title: "家常番茄炒蛋", channel_name: "厨房日记" },
    health: {
      overall_label: "部分降级",
      overall_level: "degraded",
      source: { level: "degraded", label: "数据源降级" },
      llm: { level: "ok", label: "大模型正常" },
      notes: ["运营选择跳过语音转录，内容仅基于标题/简介"],
    },
    metrics: {},
    contrast_summary: {},
    content_analysis: {
      pipeline_status: "degraded",
      skip_reason: "user_skipped_stt",
      segments: [],
      content_conclusion: {
        video_types: ["教程"],
        thesis: "根据标题的简要描述",
        tone: "解说",
        tone_description: "耐心解说",
        commercial: null,
        confidence: 0.4,
        coverage_label: "无语音转录，仅标题/简介",
      },
    },
    comment_analysis: {},
  };
  let out;
  assert.doesNotThrow(() => {
    out = adapt(result);
  }, "empty segs must not throw");
  assert.ok(out);
  assert.strictEqual(out.segs.length, 0);
  assert.strictEqual(out.contentConclusion.thesis, "根据标题的简要描述");
  assert.strictEqual(out.contentConclusion.commercial, null);
}

async function main() {
  const tests = [
    ["adapt three fixtures", testAdaptThreeFixtures],
    ["adapt(null) no TypeError", testAdaptNullNoTypeError],
    ["cache_hit missing result no TypeError", testCacheHitMissingResultNoTypeError],
    ["empty segs user_skipped_stt", testEmptySegsSkipSttNoThrow],
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