/**
 * Frontend batch import / poll / compare-CSV export.
 * Uses the same /api/v1/analyses contract as single-video mode.
 */
(function (global) {
  var VIDEO_ID_RE =
    /(?:youtube\.com\/watch\?[^#]*v=|youtu\.be\/|youtube\.com\/shorts\/|youtube\.com\/embed\/)([A-Za-z0-9_-]{11})/;
  var BARE_ID_RE = /^[A-Za-z0-9_-]{11}$/;

  var COMPARE_FIELDS = [
    "序号",
    "视频链接",
    "视频ID",
    "标题",
    "频道",
    "频道/视频说明",
    "博主层级",
    "类目",
    "主题",
    "内容概要",
    "评论风向",
    "评论观点",
    "商业状态",
    "人工测评",
    "语言",
    "short/video",
    "语气",
    "任务状态",
    "字幕模式",
    "数据来源",
    "内容备注",
    "失败原因",
    "分析工具",
    "批次编号",
  ];

  function parseVideoId(line) {
    var s = String(line || "").trim();
    if (!s || s.charAt(0) === "#") return null;
    if (BARE_ID_RE.test(s)) return s;
    var m = VIDEO_ID_RE.search ? null : null;
    m = s.match(VIDEO_ID_RE);
    return m ? m[1] : null;
  }

  function parseUrlList(text) {
    var lines = String(text || "").split(/\r?\n/);
    var out = [];
    var seen = {};
    for (var i = 0; i < lines.length; i++) {
      var raw = lines[i].trim();
      if (!raw || raw.charAt(0) === "#") continue;
      if (raw.indexOf(",") >= 0 && raw.indexOf("http") !== 0) {
        raw = raw.split(",")[0].trim().replace(/^"|"$/g, "");
      }
      var vid = parseVideoId(raw);
      if (!vid || seen[vid]) continue;
      seen[vid] = true;
      out.push({
        video_id: vid,
        url: "https://www.youtube.com/watch?v=" + vid,
      });
    }
    return out;
  }

  function pipeJoin(items) {
    if (!items || !items.length) return "";
    if (typeof items === "string") return items;
    return items
      .filter(function (x) {
        return x != null && String(x) !== "";
      })
      .join("|");
  }

  var STATUS_CN = {
    completed: "已完成",
    cache_hit: "缓存命中",
    degraded: "降级完成",
    failed: "失败",
    cancelled: "已取消",
    analyzing: "分析中",
    queued: "排队中",
  };

  function numOrNone(x) {
    if (x === null || x === undefined || x === "") return null;
    var n = Number(x);
    return isFinite(n) ? n : null;
  }

  function sentimentWind(pos, neu, neg) {
    var p = numOrNone(pos),
      n = numOrNone(neu),
      g = numOrNone(neg);
    if (p === null && n === null && g === null) return "";
    p = p === null ? 0 : p;
    n = n === null ? 0 : n;
    g = g === null ? 0 : g;
    if (p <= 1 && n <= 1 && g <= 1 && p + n + g <= 1.5) {
      p *= 100;
      n *= 100;
      g *= 100;
    }
    var dominant = "偏中性";
    if (p >= n && p >= g) dominant = "偏正面";
    else if (g >= p && g >= n) dominant = "偏负面";
    return (
      dominant +
      "（正" +
      Math.round(p) +
      "%/中" +
      Math.round(n) +
      "%/负" +
      Math.round(g) +
      "%）"
    );
  }


  function formatCommercialStatus(commercial, dataSource, skipReason, captionsMode) {
    var TYPE_ORDER = ["优惠码", "联盟分销", "购物链接", "赞助声明", "品牌植入", "课程私域", "自有产品"];
    var STATUSES = ["无信号", "疑似种草", "明确带货", "自我推广", "无法判断"];
    function pushType(arr, label) {
      if (arr.indexOf(label) < 0) arr.push(label);
    }
    function orderedTypes(arr) {
      var out = [];
      for (var i = 0; i < TYPE_ORDER.length; i++) {
        if (arr.indexOf(TYPE_ORDER[i]) >= 0) out.push(TYPE_ORDER[i]);
      }
      return out.slice(0, 2);
    }
    function isDegraded(ds, sk, cap) {
      var blob = String(ds || "") + "|" + String(sk || "") + "|" + String(cap || "");
      if (/标题\/简介|跳过字幕|跳过语音|字幕不可用|user_skipped_stt/.test(blob)) return true;
      var c = String(cap || "").toLowerCase();
      return c === "off" || c === "false" || c === "0" || c === "跳过字幕";
    }
    if (commercial == null || commercial === "") return "无信号";
    var blob = "";
    if (typeof commercial === "object") {
      var st = commercial.state || commercial.status;
      var types = commercial.types || commercial.type || [];
      if (typeof types === "string") types = [types];
      if (STATUSES.indexOf(st) >= 0) {
        types = orderedTypes(types);
        if (types.length && (st === "明确带货" || st === "疑似种草" || st === "自我推广")) {
          return st + "（" + types.join("·") + "）";
        }
        return String(st);
      }
      var parts = [];
      if (commercial.text) parts.push(String(commercial.text));
      var excerpts = commercial.excerpts || [];
      for (var ei = 0; ei < excerpts.length; ei++) {
        var ex = excerpts[ei];
        parts.push(ex && typeof ex === "object" ? String(ex.text || "") : String(ex));
      }
      blob = parts.join(" ");
    } else {
      blob = String(commercial).trim();
    }
    if (!blob) return "无信号";
    for (var si = 0; si < STATUSES.length; si++) {
      if (blob === STATUSES[si] || blob.indexOf(STATUSES[si] + "（") === 0 || blob.indexOf(STATUSES[si] + "(") === 0) {
        return blob;
      }
    }
    var hard = [];
    var soft = [];
    if (/优惠码|折扣码|discount\s*code|use\s+code/i.test(blob)) pushType(hard, "优惠码");
    if (/affiliate|联盟/i.test(blob)) pushType(hard, "联盟分销");
    if (/购买链接|专属链接|购物链接|shop\s*link/i.test(blob)) pushType(hard, "购物链接");
    if (/#\s*ad\b|#\s*sponsored\b|paid\s+partnership|sponsored\s+by|付费推广|包含付费推广/i.test(blob)) pushType(hard, "赞助声明");
    if (/平台标记.{0,6}付费产品植入|付费产品植入/i.test(blob)) pushType(hard, "品牌植入");
    if (/赞助|商单|软广|硬广|带货|植入广告|广告植入|品牌合作|商务合作|商业合作|品牌提供/i.test(blob)) pushType(soft, "品牌植入");
    var isSelf = /自我推广|个人开发|我开发的|我的课程|自有(?:产品|App|应用)|自己的(?:品牌|产品|课)|本频道|作者本人|我司产品/i.test(blob);
    var hasHard = hard.length > 0 || /优惠码|折扣码|discount\s*code|use\s+code|affiliate|#\s*ad\b|#\s*sponsored\b|paid\s+partnership|sponsored\s+by|付费推广|购买链接|专属链接|付费产品植入/i.test(blob);
    var hasSoft = soft.length > 0 || /赞助|商单|软广|硬广|带货|植入|品牌合作|商务合作|商业合作|商单线索/i.test(blob);
    var degraded = isDegraded(dataSource, skipReason, captionsMode);
    if (isSelf && !hasHard) {
      var selfTypes = orderedTypes(["自有产品"].concat(soft));
      return selfTypes.length ? "自我推广（" + selfTypes.join("·") + "）" : "自我推广";
    }
    if (hasHard) {
      var ht = orderedTypes(hard.concat(soft));
      return ht.length ? "明确带货（" + ht.join("·") + "）" : "明确带货";
    }
    if (hasSoft) {
      var stypes = orderedTypes(soft);
      return stypes.length ? "疑似种草（" + stypes.join("·") + "）" : "疑似种草";
    }
    if (degraded) return "无法判断";
    if (blob.length >= 12) return "疑似种草";
    return "无法判断";
  }

  function captionsCn(mode) {
    var m = String(mode || "").toLowerCase();
    if (m === "off" || m === "false" || m === "0") return "跳过字幕";
    if (m === "on" || m === "true" || m === "1") return "拉取字幕";
    return mode || "";
  }

  function contentDataSource(result, captionsMode) {
    result = result || {};
    var health = result.health || {};
    var tr = health.transcript || {};
    var mode = String(tr.mode || "").toLowerCase();
    var notes = health.notes || [];
    var noteBlob = notes.join("|");
    var ca = result.content_analysis || {};
    var skipReason = String(ca.skip_reason || "");
    var cap = String(captionsMode || "").toLowerCase();
    if (mode === "speech_to_text") return "语音转录（Whisper）";
    if (mode === "captions") {
      if (noteBlob.indexOf("人工字幕") >= 0) return "公开字幕（人工）";
      if (noteBlob.indexOf("自动字幕") >= 0 || noteBlob.indexOf("ASR") >= 0) return "公开字幕（自动ASR）";
      return "公开字幕";
    }
    if (cap === "off" || cap === "false" || cap === "0" || noteBlob.indexOf("跳过字幕") >= 0 || noteBlob.indexOf("未拉取字幕") >= 0) {
      if (noteBlob.indexOf("跳过语音转录") >= 0 || skipReason === "user_skipped_stt") return "标题/简介（跳过语音转录）";
      return "标题/简介（跳过字幕）";
    }
    if (noteBlob.indexOf("跳过语音转录") >= 0 || skipReason === "user_skipped_stt") return "标题/简介（跳过语音转录）";
    if (
      noteBlob.indexOf("无字幕") >= 0 ||
      noteBlob.indexOf("字幕未拿到") >= 0 ||
      noteBlob.indexOf("无法从 YouTube 拉取字幕") >= 0 ||
      noteBlob.indexOf("限流") >= 0 ||
      skipReason ||
      mode === "none" ||
      mode === "failed" ||
      mode === ""
    ) {
      return "标题/简介（字幕不可用）";
    }
    return "未知";
  }

  function resultToCompareRow(rec, runId, rowIndex) {
    var result = rec.result || {};
    var video = result.video || {};
    var ca = result.content_analysis || {};
    var ka = result.comment_analysis || {};
    var cc = ca.content_conclusion || {};
    var kc = ka.comment_conclusion || {};
    var sent = ka.sentiment || {};
    var themes = ka.open_themes || [];
    var themeNames = themes.map(function (t) {
      return t && t.name;
    });
    var err = rec.error;
    var errCode = "";
    if (err && typeof err === "object") errCode = String(err.code || err.error || "");
    else if (err) errCode = String(err);
    var thesis = String(cc.thesis || "")
      .replace(/\n/g, " ")
      .trim();
    var doing = String(kc.doing || "")
      .replace(/\n/g, " ")
      .trim();
    var themeS = pipeJoin(themeNames);
    var commentView = doing && themeS ? doing + "；主题：" + themeS : doing || themeS;
    var title = video.title || "";
    var channel = video.channel_name || "";
    var desc = thesis || title;
    var capMode = rec.skip_captions ? "off" : rec.captions_mode || "on";
    var dataSource = contentDataSource(result, capMode);
    var commercialS = formatCommercialStatus(cc.commercial, dataSource, ca.skip_reason || "", capMode);
    return {
      序号: rowIndex || "",
      视频链接: video.canonical_url || rec.url || "",
      视频ID: rec.video_id || video.video_id || "",
      标题: title,
      频道: channel,
      "频道/视频说明": desc,
      博主层级: video.blogger_tier || "",
      类目: pipeJoin(cc.video_types),
      主题: pipeJoin(cc.topics),
      内容概要: thesis,
      评论风向: sentimentWind(sent.positive_pct, sent.neutral_pct, sent.negative_pct),
      评论观点: commentView,
      商业状态: commercialS,
      人工测评: "",
      语言: video.language || "",
      "short/video": video.format_kind || "",
      语气: cc.tone_description || cc.tone || "",
      任务状态: STATUS_CN[rec.status] || rec.status || "",
      字幕模式: captionsCn(capMode),
      数据来源: dataSource,
      内容备注: ca.skip_reason || "",
      失败原因: errCode,
      分析工具: "video-analyzer",
      批次编号: runId || rec.run_id || "",
    };
  }


  function csvEscape(v) {
    var s = String(v == null ? "" : v);
    if (/[",\n\r]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  function rowsToCsv(rows) {
    var lines = [COMPARE_FIELDS.join(",")];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      lines.push(
        COMPARE_FIELDS.map(function (k) {
          return csvEscape(r[k]);
        }).join(",")
      );
    }
    return "\uFEFF" + lines.join("\r\n");
  }

  function downloadText(filename, text, mime) {
    var blob = new Blob([text], { type: mime || "text/csv;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      URL.revokeObjectURL(a.href);
      a.remove();
    }, 500);
  }

  function resolveApiRoot(options) {
    if (options && options.apiRoot) return options.apiRoot;
    if (typeof global.apiRoot === "function") return global.apiRoot();
    try {
      return new URL("api", document.baseURI).pathname.replace(/\/$/, "");
    } catch (e) {
      return "/api";
    }
  }

  function sleep(ms, signal) {
    return new Promise(function (resolve, reject) {
      if (signal && signal.aborted) {
        reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
        return;
      }
      var t = setTimeout(resolve, ms);
      if (signal) {
        signal.addEventListener(
          "abort",
          function () {
            clearTimeout(t);
            reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
          },
          { once: true }
        );
      }
    });
  }

  async function analyzeOne(item, opts) {
    var apiRoot = opts.apiRoot;
    var force = !!opts.force;
    var pollInterval = opts.pollIntervalMs != null ? opts.pollIntervalMs : 1000;
    var timeoutMs = opts.timeoutMs != null ? opts.timeoutMs : 600000;
    var signal = opts.signal;
    var onProgress = opts.onProgress;
    var t0 = Date.now();

    function timedOut() {
      return Date.now() - t0 >= timeoutMs;
    }

    var res = await fetch(apiRoot + "/v1/analyses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: item.url, force: force, skip_captions: !!opts.skipCaptions, auto_skip_stt: true, batch: true }),
      signal: signal,
    });
    var body = await res.json().catch(function () {
      return null;
    });

    if (res.status === 400) {
      return {
        video_id: item.video_id,
        url: item.url,
        status: "failed",
        error: (body && body.error) || { code: "invalid_url", message: (body && body.message) || "invalid url" },
        result: null,
        latency_ms: Date.now() - t0,
      };
    }

    if (res.status === 200 && body && body.cache_hit) {
      return {
        video_id: item.video_id,
        url: item.url,
        task_id: body.task_id,
        status: "cache_hit",
        error: null,
        result: body.result || null,
        latency_ms: Date.now() - t0,
      };
    }

    if (!(res.status === 202 || res.status === 200) || !body || !body.task_id) {
      return {
        video_id: item.video_id,
        url: item.url,
        status: "failed",
        error: (body && body.error) || { code: "start_failed", message: "无法创建任务" },
        result: body,
        latency_ms: Date.now() - t0,
      };
    }

    var taskId = body.task_id;
    var last = body;
    while (true) {
      if (signal && signal.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
      if (timedOut()) {
        return {
          video_id: item.video_id,
          url: item.url,
          task_id: taskId,
          status: "failed",
          error: { code: "poll_timeout", message: "分析超时" },
          result: null,
          latency_ms: Date.now() - t0,
        };
      }
      if (typeof onProgress === "function") {
        try {
          onProgress(last);
        } catch (e) {}
      }
      var st = last.status;
      if (st === "completed" || st === "cache_hit" || st === "failed") {
        return {
          video_id: item.video_id,
          url: item.url,
          task_id: taskId,
          status: st,
          error: last.error || null,
          result: last.result || null,
          preview: last.preview || null,
          latency_ms: Date.now() - t0,
        };
      }
      await sleep(pollInterval, signal);
      var pollRes = await fetch(apiRoot + "/v1/analyses/" + encodeURIComponent(taskId), { signal: signal });
      last = await pollRes.json().catch(function () {
        return { status: "failed", error: { code: "poll_error", message: "轮询失败" } };
      });
    }
  }


  var TRANSIENT_RETRY_BUCKETS = { network: true, proxy: true, timeout: true };

  function errorText(err) {
    if (!err) return "";
    if (typeof err === "string") return err;
    if (typeof err === "object") {
      return String((err.code || "") + " " + (err.message || "") + " " + (err.error || ""));
    }
    return String(err);
  }

  function classifyTransientBucket(rec, thrown) {
    var msg = "";
    if (thrown) {
      if (thrown.name === "AbortError") return "";
      msg = errorText(thrown.message || thrown);
    } else if (rec) {
      msg = errorText(rec.error) + " " + String(rec.status || "");
      if (rec.error && rec.error.code) msg += " " + rec.error.code;
    }
    var low = msg.toLowerCase();
    if (low.indexOf("proxy") >= 0 || low.indexOf("socks") >= 0 || low.indexOf("youtube_proxy_failed") >= 0) {
      return "proxy";
    }
    if (
      low.indexOf("poll_timeout") >= 0 ||
      low.indexOf("timeout") >= 0 ||
      low.indexOf("etimedout") >= 0
    ) {
      return "timeout";
    }
    if (
      low.indexOf("network") >= 0 ||
      low.indexOf("failed to fetch") >= 0 ||
      low.indexOf("fetch failed") >= 0 ||
      low.indexOf("econn") >= 0 ||
      low.indexOf("enotfound") >= 0 ||
      low.indexOf("youtube_network_failed") >= 0 ||
      low.indexOf("poll_error") >= 0 ||
      low.indexOf("load failed") >= 0
    ) {
      return "network";
    }
    return "";
  }

  function isTransientRetryable(rec, thrown) {
    var bucket = classifyTransientBucket(rec, thrown);
    return !!(bucket && TRANSIENT_RETRY_BUCKETS[bucket]);
  }

  function createBatchSession(options) {
    options = options || {};
    var controller = null;
    var busy = false;
    var records = [];
    var runId = "";

    function abort() {
      if (controller) {
        try {
          controller.abort();
        } catch (e) {}
      }
      busy = false;
    }

    function isBusy() {
      return busy;
    }

    function getRecords() {
      return records.slice();
    }

    function getRunId() {
      return runId;
    }

    async function run(items, runOpts) {
      runOpts = runOpts || {};
      abort();
      controller = new AbortController();
      busy = true;
      records = items.map(function (it) {
        return {
          video_id: it.video_id,
          url: it.url,
          status: "queued",
          step_label: "排队",
          error: null,
          result: null,
          latency_ms: null,
          task_id: null,
        };
      });
      runId =
        new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z") +
        "-" +
        Math.random().toString(16).slice(2, 8);
      var concurrency = Math.max(1, Math.min(4, Number(runOpts.concurrency) || 2));
      var apiRoot = resolveApiRoot(runOpts);
      var onUpdate = runOpts.onUpdate;
      var force = !!runOpts.force;
      var skipCaptions = runOpts.skipCaptions !== false; // default true for batch
      var signal = controller.signal;

      function notify() {
        if (typeof onUpdate === "function") {
          try {
            onUpdate(records.slice(), { runId: runId, busy: busy });
          } catch (e) {}
        }
      }
      notify();

      var retryMax = Math.max(0, Math.min(3, Number(runOpts.retryMax != null ? runOpts.retryMax : 2)));
      var retryTransient = runOpts.retryTransient !== false;
      var retryDelaysMs = [3000, 8000, 15000];
      var idx = 0;
      async function worker() {
        while (idx < items.length) {
          if (signal.aborted) return;
          var my = idx++;
          var item = items[my];
          records[my].status = "analyzing";
          records[my].step_label = "启动中";
          records[my].retry_count = 0;
          notify();
          var attempt = 0;
          var done = false;
          while (!done) {
            if (signal.aborted) {
              records[my].status = "cancelled";
              records[my].step_label = "已取消";
              break;
            }
            try {
              if (attempt > 0) {
                records[my].status = "analyzing";
                records[my].step_label = "重试 " + attempt + "/" + retryMax;
                records[my].retry_count = attempt;
                notify();
                var delay = retryDelaysMs[Math.min(attempt - 1, retryDelaysMs.length - 1)];
                await sleep(delay, signal);
              } else {
                records[my].step_label = "启动中";
                notify();
              }
              var rec = await analyzeOne(item, {
                apiRoot: apiRoot,
                force: force,
                skipCaptions: skipCaptions,
                signal: signal,
                onProgress: function (body) {
                  if (body && body.current_step_label) records[my].step_label = body.current_step_label;
                  else if (body && body.status) records[my].step_label = body.status;
                  notify();
                },
              });
              var transient = retryTransient && rec && rec.status === "failed" && isTransientRetryable(rec, null);
              if (transient && attempt < retryMax) {
                attempt += 1;
                records[my].error = rec.error || null;
                records[my].step_label = "将重试 (" + classifyTransientBucket(rec, null) + ")";
                notify();
                continue;
              }
              records[my] = Object.assign({}, records[my], rec, {
                run_id: runId,
                skip_captions: skipCaptions,
                retry_count: attempt,
              });
              done = true;
            } catch (e) {
              if (e && e.name === "AbortError") {
                records[my].status = "cancelled";
                records[my].step_label = "已取消";
                done = true;
              } else if (retryTransient && isTransientRetryable(null, e) && attempt < retryMax) {
                attempt += 1;
                records[my].status = "analyzing";
                records[my].error = { code: "client", message: String(e && e.message ? e.message : e) };
                records[my].step_label = "将重试 (" + classifyTransientBucket(null, e) + ")";
                notify();
              } else {
                records[my].status = "failed";
                records[my].error = { code: "client", message: String(e && e.message ? e.message : e) };
                records[my].step_label = "失败";
                records[my].retry_count = attempt;
                done = true;
              }
            }
          }
          notify();
        }
      }

      var workers = [];
      for (var w = 0; w < concurrency; w++) workers.push(worker());
      try {
        await Promise.all(workers);
      } finally {
        busy = false;
        notify();
      }
      return records.slice();
    }

    function exportCompareCsv(filename) {
      var rows = records.map(function (r, i) {
        return resultToCompareRow(r, runId, i + 1);
      });
      downloadText(filename || "ours_compare_" + (runId || "batch") + ".csv", rowsToCsv(rows), "text/csv;charset=utf-8");
    }

    function exportJsonl(filename) {
      var lines = records.map(function (r) {
        return JSON.stringify(r);
      });
      downloadText(
        filename || "ours_" + (runId || "batch") + ".jsonl",
        lines.join("\n") + "\n",
        "application/x-ndjson;charset=utf-8"
      );
    }

    return {
      parseUrlList: parseUrlList,
      run: run,
      abort: abort,
      isBusy: isBusy,
      getRecords: getRecords,
      getRunId: getRunId,
      exportCompareCsv: exportCompareCsv,
      exportJsonl: exportJsonl,
      COMPARE_FIELDS: COMPARE_FIELDS,
    };
  }

  global.createBatchSession = createBatchSession;
  global.parseBatchUrlList = parseUrlList;
})(typeof window !== "undefined" ? window : globalThis);
