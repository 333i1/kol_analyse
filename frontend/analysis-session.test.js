"use strict";

const assert = require("assert");
const {
  createAnalysisSession,
  POLL_CAP_MS,
  TRANSIENT_RETRY_LIMIT,
} = require("./analysis-session.js");

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

/** Caller that only treats {ok:true} as success — models renderResults gating. */
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
  const session = createAnalysisSession({
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
  const session = createAnalysisSession({
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
  live[live.length - 1].resolve(jsonResponse({ status: "completed", result: { x: 2 } }));
  const r2 = await callerTreatAsSuccess(p2);
  assert.strictEqual(r2.success, true);
  assert.strictEqual(r2.body.result.x, 2);
}

async function testTimeoutPathFailure() {
  let t = 0;
  const fetch = async () => jsonResponse({ status: "analyzing", current_step: 1 });
  const session = createAnalysisSession({
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
  const session = createAnalysisSession({
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
  const session = createAnalysisSession({
    fetch,
    sleep: async () => {},
  });
  const treated = await callerTreatAsSuccess(session.poll("x"));
  assert.strictEqual(treated.success, true);
  assert.strictEqual(n, 2);
}

async function testGoBtnDisabledWhilePolling() {
  const btn = { disabled: false };
  let release;
  const fetch = () =>
    new Promise((resolve) => {
      release = resolve;
    });
  const session = createAnalysisSession({
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

async function testNoTwoParallelPolls() {
  const btn = { disabled: false };
  let nInflight = 0;
  let maxInflight = 0;
  const fetch = () =>
    new Promise((resolve) => {
      nInflight += 1;
      if (nInflight > maxInflight) maxInflight = nInflight;
      setImmediate(() => {
        nInflight -= 1;
        resolve(jsonResponse({ status: "completed", result: {} }));
      });
    });
  const session = createAnalysisSession({
    fetch,
    goBtn: btn,
    sleep: async () => {},
  });
  const p1 = session.poll("a");
  assert.strictEqual(btn.disabled, true);
  const p2 = session.poll("b");
  const r1 = await callerTreatAsSuccess(p1);
  const r2 = await callerTreatAsSuccess(p2);
  assert.strictEqual(r1.success, false);
  assert.strictEqual(r2.success, true);
  assert.ok(maxInflight <= 2);
  assert.strictEqual(session.isPolling(), false);
}


async function testAwaitingPauseKeepsBusyAndNotCompleted() {
  const btn = { disabled: false };
  let n = 0;
  const fetch = async () => {
    n += 1;
    if (n === 1) {
      return jsonResponse({
        status: "analyzing",
        awaiting: "transcript_choice",
        current_step: 2,
        current_step_label: "等待是否语音转录",
      });
    }
    return jsonResponse({ status: "completed", result: { ok: 1 } });
  };
  const session = createAnalysisSession({
    fetch,
    goBtn: btn,
    sleep: async () => {},
  });
  const first = await session.poll("t-wait");
  assert.strictEqual(first.ok, true);
  assert.strictEqual(first.awaiting, "transcript_choice");
  assert.ok(first.body.status !== "completed");
  assert.ok(first.body.status !== "failed");
  assert.strictEqual(btn.disabled, true, "goBtn stays disabled while awaiting");
  const second = await session.poll("t-wait", { keepSession: true });
  const treated = await callerTreatAsSuccess(Promise.resolve(second));
  assert.strictEqual(treated.success, true);
  assert.strictEqual(btn.disabled, false);
}

async function main() {
  const tests = [
    ["abort stale resolve not success", testAbortStaleResolveNotSuccess],
    ["second poll aborts first", testSecondPollAbortsFirst],
    ["timeout path failure", testTimeoutPathFailure],
    ["retry 3 then fail", testRetryThenFail],
    ["one transient then success", testOneTransientThenSuccess],
    ["goBtn disabled while polling", testGoBtnDisabledWhilePolling],
    ["no two parallel polls", testNoTwoParallelPolls],
    ["awaiting transcript_choice pause", testAwaitingPauseKeepsBusyAndNotCompleted],
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
