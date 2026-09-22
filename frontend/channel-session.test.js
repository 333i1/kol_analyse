"use strict";

const assert = require("assert");
const {
  createChannelSession,
  POLL_CAP_MS,
  TRANSIENT_RETRY_LIMIT,
} = require("./channel-session.js");

function abortError() {
  const e = new Error("aborted");
  e.name = "AbortError";
  return e;
}

function jsonResponse(body, ok, status) {
  return {
    ok: ok !== false,
    status: status == null ? 200 : status,
    json: async () => body,
  };
}

/** Caller that only treats {ok:true} as success — models render gating. */
async function callerTreatAsSuccess(promise) {
  const outcome = await promise;
  if (outcome && outcome.ok && outcome.body) return { success: true, body: outcome.body };
  return { success: false, outcome };
}

async function testAbortStaleResolveNotSuccess() {
  let fetchResolve;
  const fetch = () =>
    new Promise((resolve) => {
      fetchResolve = resolve;
    });
  const session = createChannelSession({
    fetch,
    sleep: async () => {},
  });
  const p = session.poll("task-old");
  session.abort();
  fetchResolve(jsonResponse({ status: "completed", result: { id: "stale" } }));
  const treated = await callerTreatAsSuccess(p);
  assert.strictEqual(treated.success, false, "stale completed GET must not be success");
  assert.strictEqual(treated.outcome.ok, false);
  assert.strictEqual(treated.outcome.cancelled, true);
}

async function testSecondPollAbortsFirst() {
  const fetches = [];
  const fetch = (url, opts) =>
    new Promise((resolve, reject) => {
      const rec = { url, opts, resolve, reject };
      fetches.push(rec);
      if (opts && opts.signal) {
        opts.signal.addEventListener(
          "abort",
          () => reject(abortError()),
          { once: true }
        );
      }
    });
  const session = createChannelSession({
    fetch,
    sleep: async () => {},
  });
  const p1 = session.poll("t1");
  const p2 = session.poll("t2");
  const r1 = await callerTreatAsSuccess(p1);
  assert.strictEqual(r1.success, false);
  assert.strictEqual(r1.outcome.cancelled, true);
  const live = fetches.filter((f) => !(f.opts && f.opts.signal && f.opts.signal.aborted));
  assert.ok(live.length >= 1, "second poll should have an in-flight GET");
  assert.ok(
    String(live[live.length - 1].url).indexOf("/v1/channel-analyses/") >= 0,
    "poll URL must be channel-analyses"
  );
  live[live.length - 1].resolve(jsonResponse({ status: "completed", result: { x: 2 } }));
  const r2 = await callerTreatAsSuccess(p2);
  assert.strictEqual(r2.success, true);
  assert.strictEqual(r2.body.result.x, 2);
}

async function testTimeoutPathFailure() {
  let t = 0;
  const fetch = async () => jsonResponse({ status: "analyzing", current_step: 1 });
  const session = createChannelSession({
    fetch,
    now: () => t,
    sleep: async () => {
      t += POLL_CAP_MS;
    },
  });
  const treated = await callerTreatAsSuccess(session.poll("slow"));
  assert.strictEqual(treated.success, false);
  assert.strictEqual(treated.outcome.timeout, true);
  assert.strictEqual(treated.outcome.ok, false);
}

async function testRetryThenFail() {
  let n = 0;
  const fetch = async () => {
    n += 1;
    throw new Error("network down");
  };
  const session = createChannelSession({
    fetch,
    sleep: async () => {},
  });
  const treated = await callerTreatAsSuccess(session.poll("x"));
  assert.strictEqual(treated.success, false);
  assert.ok(!treated.outcome.cancelled);
  assert.ok(!treated.outcome.timeout);
  assert.strictEqual(n, TRANSIENT_RETRY_LIMIT + 1);
}

async function testOneTransientThenSuccess() {
  let n = 0;
  const fetch = async () => {
    n += 1;
    if (n === 1) throw new Error("blip");
    return jsonResponse({ status: "completed", result: {} });
  };
  const session = createChannelSession({
    fetch,
    sleep: async () => {},
  });
  const treated = await callerTreatAsSuccess(session.poll("x"));
  assert.strictEqual(treated.success, true);
  assert.strictEqual(n, 2);
}

async function testTerminalDegradedIsSuccess() {
  const fetch = async () =>
    jsonResponse({ status: "degraded", result: { health: { notes: ["部分失败"] } } });
  const session = createChannelSession({
    fetch,
    sleep: async () => {},
  });
  const treated = await callerTreatAsSuccess(session.poll("deg"));
  assert.strictEqual(treated.success, true);
  assert.strictEqual(treated.body.status, "degraded");
}

async function testTerminalFailedIsSuccessForPolling() {
  const fetch = async () =>
    jsonResponse({
      status: "failed",
      error: { code: "channel_fetch_failed", message: "拉取失败" },
    });
  const session = createChannelSession({
    fetch,
    sleep: async () => {},
  });
  const treated = await callerTreatAsSuccess(session.poll("fail"));
  assert.strictEqual(treated.success, true);
  assert.strictEqual(treated.body.status, "failed");
}

async function testKeepPollingWhileAnalyzing() {
  let n = 0;
  const fetch = async () => {
    n += 1;
    if (n < 3) return jsonResponse({ status: "analyzing", result: { videos: [] } });
    return jsonResponse({ status: "completed", result: { ok: 1 } });
  };
  const session = createChannelSession({
    fetch,
    sleep: async () => {},
  });
  const progress = [];
  const treated = await callerTreatAsSuccess(
    session.poll("run", {
      onProgress: (body) => progress.push(body.status),
    })
  );
  assert.strictEqual(treated.success, true);
  assert.ok(progress.indexOf("analyzing") >= 0);
  assert.ok(n >= 3);
}

async function testGoBtnDisabledWhilePolling() {
  const btn = { disabled: false };
  let release;
  const fetch = () =>
    new Promise((resolve) => {
      release = resolve;
    });
  const session = createChannelSession({
    fetch,
    goBtn: btn,
    sleep: async () => {},
  });
  const p = session.poll("x");
  assert.strictEqual(btn.disabled, true);
  assert.strictEqual(session.isPolling(), true);
  release(jsonResponse({ status: "completed", result: {} }));
  await p;
  assert.strictEqual(btn.disabled, false);
  assert.strictEqual(session.isPolling(), false);
}

async function main() {
  const tests = [
    ["abort stale resolve not success", testAbortStaleResolveNotSuccess],
    ["second poll aborts first", testSecondPollAbortsFirst],
    ["timeout path failure", testTimeoutPathFailure],
    ["retry 3 then fail", testRetryThenFail],
    ["one transient then success", testOneTransientThenSuccess],
    ["terminal degraded is success", testTerminalDegradedIsSuccess],
    ["terminal failed is success for polling", testTerminalFailedIsSuccessForPolling],
    ["keep polling while analyzing", testKeepPollingWhileAnalyzing],
    ["goBtn disabled while polling", testGoBtnDisabledWhilePolling],
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
