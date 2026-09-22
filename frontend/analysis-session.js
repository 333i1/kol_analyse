/**
 * T070: cancellable analysis polling session.
 * Generation token + AbortController. Does not render five-zones.
 */

function resolveApiRoot(options) {
  options = options || {};
  if (options.apiRoot) return String(options.apiRoot).replace(/\/$/, "");
  if (typeof document !== "undefined" && document.baseURI) {
    try {
      return new URL("api", document.baseURI).pathname.replace(/\/$/, "");
    } catch (e) {}
  }
  return "/api";
}

(function (global) {
  "use strict";

  var POLL_CAP_MS = 600000;
  var TRANSIENT_RETRY_LIMIT = 3;
  var POLL_INTERVAL_MS = 1000;

  function isAbortError(err) {
    if (!err) return false;
    if (err.name === "AbortError") return true;
    if (err.code === 20) return true;
    return false;
  }

  function defaultSleep(ms, signal) {
    return new Promise(function (resolve, reject) {
      if (signal && signal.aborted) {
        var e = new Error("aborted");
        e.name = "AbortError";
        reject(e);
        return;
      }
      var timer = setTimeout(function () {
        resolve();
      }, ms);
      if (signal) {
        signal.addEventListener(
          "abort",
          function () {
            clearTimeout(timer);
            var e2 = new Error("aborted");
            e2.name = "AbortError";
            reject(e2);
          },
          { once: true }
        );
      }
    });
  }

  function createAnalysisSession(options) {
    options = options || {};
    var fetchImpl =
      options.fetch ||
      (typeof global.fetch === "function" ? global.fetch.bind(global) : null);
    var nowFn = options.now || function () { return Date.now(); };
    var sleepFn = options.sleep || defaultSleep;
    var pollCapMs = options.pollCapMs != null ? options.pollCapMs : POLL_CAP_MS;
    var retryLimit =
      options.transientRetryLimit != null
        ? options.transientRetryLimit
        : TRANSIENT_RETRY_LIMIT;
    var intervalMs =
      options.pollIntervalMs != null ? options.pollIntervalMs : POLL_INTERVAL_MS;
    var goBtn = options.goBtn || null;
    var apiRoot = resolveApiRoot(options);

    var generation = 0;
    var controller = null;
    var polling = false;

    function setBusy(busy) {
      polling = !!busy;
      if (goBtn) goBtn.disabled = !!busy;
    }

    function abort() {
      generation += 1;
      if (controller) {
        try {
          controller.abort();
        } catch (e) {}
        controller = null;
      }
      setBusy(false);
      return generation;
    }

    function isPolling() {
      return polling;
    }

    function currentGeneration() {
      return generation;
    }

    async function poll(taskId, pollOpts) {
      pollOpts = pollOpts || {};
      var onProgress = pollOpts.onProgress;
      var keepSession = !!pollOpts.keepSession;
      if (!keepSession) {
        abort();
        controller = new AbortController();
        setBusy(true);
      } else {
        if (!controller || (controller.signal && controller.signal.aborted)) {
          controller = new AbortController();
        }
        setBusy(true);
      }
      var myGen = generation;
      var signal = controller.signal;
      var started = nowFn();
      var transientFails = 0;
      var pauseAwaiting = false;

      function stillCurrent() {
        return myGen === generation;
      }

      try {
        while (true) {
          if (!stillCurrent()) return { ok: false, cancelled: true };
          if (nowFn() - started >= pollCapMs) {
            return { ok: false, timeout: true };
          }
          try {
            var res = await fetchImpl(
              apiRoot + "/v1/analyses/" + encodeURIComponent(taskId),
              { signal: signal }
            );
            if (!stillCurrent()) return { ok: false, cancelled: true };
            var body;
            try {
              body = await res.json();
            } catch (parseErr) {
              if (!stillCurrent()) return { ok: false, cancelled: true };
              if (isAbortError(parseErr)) {
                return { ok: false, cancelled: true };
              }
              throw parseErr;
            }
            if (!stillCurrent()) return { ok: false, cancelled: true };

            if (!res.ok) {
              return { ok: false, status: res.status, body: body };
            }
            transientFails = 0;
            var st = body && body.status;
            if (typeof onProgress === "function") {
              try {
                onProgress(body);
              } catch (ignored) {}
            }
            if (body && body.awaiting === "transcript_choice") {
              pauseAwaiting = true;
              return { ok: true, awaiting: "transcript_choice", body: body };
            }
            if (st === "completed" || st === "cache_hit" || st === "failed") {
              return { ok: true, body: body };
            }
            await sleepFn(intervalMs, signal);
          } catch (err) {
            if (!stillCurrent()) return { ok: false, cancelled: true };
            if (isAbortError(err)) {
              if (nowFn() - started >= pollCapMs) {
                return { ok: false, timeout: true };
              }
              return { ok: false, cancelled: true };
            }
            transientFails += 1;
            if (transientFails > retryLimit) {
              return { ok: false, error: err };
            }
            try {
              await sleepFn(intervalMs, signal);
            } catch (sleepErr) {
              if (!stillCurrent()) return { ok: false, cancelled: true };
              if (isAbortError(sleepErr)) {
                return { ok: false, cancelled: true };
              }
              throw sleepErr;
            }
          }
        }
      } finally {
        if (stillCurrent()) {
          if (!pauseAwaiting) {
            setBusy(false);
            controller = null;
          }
        }
      }
    }

    return {
      poll: poll,
      abort: abort,
      isPolling: isPolling,
      generation: currentGeneration,
    };
  }

  var api = {
    createAnalysisSession: createAnalysisSession,
    POLL_CAP_MS: POLL_CAP_MS,
    TRANSIENT_RETRY_LIMIT: TRANSIENT_RETRY_LIMIT,
    POLL_INTERVAL_MS: POLL_INTERVAL_MS,
  };
  global.createAnalysisSession = createAnalysisSession;
  global.AnalysisSession = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(
  typeof window !== "undefined"
    ? window
    : typeof globalThis !== "undefined"
      ? globalThis
      : this
);
