/**
 * ChannelBriefResult (API) → safe view model for channel UI.
 * Never invent brand / pricing / 建议合作. Hide commercial when absent.
 */
(function (global) {
  "use strict";

  function formatCount(n) {
    if (n == null || n === "" || Number.isNaN(Number(n))) return "—";
    var x = Number(n);
    if (x >= 100000000) {
      var yi = x / 100000000;
      return (yi >= 10 ? Math.round(yi) : Math.round(yi * 10) / 10) + " 亿";
    }
    if (x >= 10000) {
      var wan = x / 10000;
      return (wan >= 100 ? Math.round(wan) : Math.round(wan * 10) / 10) + " 万";
    }
    if (x >= 1000) {
      return String(Math.round(x));
    }
    return String(Math.round(x * 10) / 10 === Math.round(x) ? Math.round(x) : Math.round(x * 10) / 10);
  }

  function formatDuration(seconds) {
    if (seconds == null || Number.isNaN(Number(seconds))) return "—";
    var s = Math.max(0, Math.round(Number(seconds)));
    var m = Math.floor(s / 60);
    var r = s % 60;
    return m + ":" + String(r).padStart(2, "0");
  }

  function formatRate(rate) {
    if (rate == null || Number.isNaN(Number(rate))) return "—";
    var pct = Number(rate) * 100;
    if (pct >= 10) return Math.round(pct) + "%";
    return (Math.round(pct * 10) / 10) + "%";
  }

  function formatPct(n) {
    if (n == null || Number.isNaN(Number(n))) return "—";
    return Math.round(Number(n)) + "%";
  }

  function pipelineLabel(status, notes) {
    var st = status || "";
    if (st === "completed" || st === "cache_hit") return { text: "完成", level: "ok" };
    if (st === "degraded") {
      var note = (notes && notes[0]) || "降级";
      return { text: note, level: "warn" };
    }
    if (st === "failed") {
      return { text: (notes && notes[0]) || "失败", level: "bad" };
    }
    if (st === "analyzing" || st === "running") return { text: "分析中", level: "run" };
    if (st === "queued" || st === "pending") return { text: "排队", level: "wait" };
    return { text: st || "—", level: "wait" };
  }

  function countsToBars(counts, totalHint) {
    var entries = Object.keys(counts || {}).map(function (k) {
      return { name: k, n: Number(counts[k]) || 0 };
    });
    entries.sort(function (a, b) { return b.n - a.n; });
    var total = totalHint;
    if (total == null) {
      total = entries.reduce(function (s, e) { return s + e.n; }, 0);
    }
    total = total || 1;
    return entries.map(function (e) {
      return {
        name: e.name,
        n: e.n,
        total: total,
        pct: Math.round((e.n / total) * 100),
      };
    });
  }

  function adaptChannelBrief(result) {
    if (!result) return null;

    var channel = result.channel || {};
    var sample = result.sample || {};
    var health = result.health || {};
    var metrics = result.metrics || {};
    var rvh = result.recent_vs_hot || {};
    var content = result.content_habits || {};
    var audience = result.audience_habits || {};
    var alignment = result.alignment || {};
    var videos = Array.isArray(result.videos) ? result.videos : [];
    var ops = result.ops || {};
    var commercialRaw = result.commercial;

    var sampleUsed = metrics.sample_count_used != null
      ? metrics.sample_count_used
      : (sample.video_ids && sample.video_ids.length) || videos.length || 0;

    var contentTotal = health.content_total != null ? health.content_total : sampleUsed;
    var commentsTotal = health.comments_total != null ? health.comments_total : sampleUsed;
    var contentOk = health.content_ok != null ? health.content_ok : 0;
    var commentsOk = health.comments_ok != null ? health.comments_ok : 0;
    var autoCap = health.auto_caption_count || 0;
    var failedIds = health.failed_video_ids || [];

    var healthStatuses = [];
    healthStatuses.push({
      text: "内容 " + contentOk + "/" + contentTotal + " 完成",
      level: contentOk >= contentTotal && contentTotal > 0 ? "ok" : (contentOk > 0 ? "warn" : "run"),
    });
    healthStatuses.push({
      text: "评论 " + commentsOk + "/" + commentsTotal + " 完成",
      level: commentsOk >= commentsTotal && commentsTotal > 0 ? "ok" : (commentsOk > 0 ? "warn" : "run"),
    });
    if (autoCap > 0) {
      healthStatuses.push({ text: autoCap + " 条自动字幕", level: "warn" });
    }
    if (failedIds.length > 0) {
      healthStatuses.push({ text: failedIds.length + " 条失败", level: "bad" });
    }

    var typeBars = countsToBars(content.type_counts, sampleUsed || null);
    var toneBars = countsToBars(content.tone_counts, sampleUsed || null);
    var ctaRate = content.cta_purchase_rate;
    var adCount = content.ad_overlay_count || 0;
    var habitExtras = [];
    if (ctaRate != null) {
      habitExtras.push({
        name: "购买 CTA",
        n: Math.round(Number(ctaRate) * (sampleUsed || 5)),
        total: sampleUsed || 5,
        pct: Math.round(Number(ctaRate) * 100),
        tone: "green",
      });
    }
    if (adCount > 0 || adCount === 0) {
      habitExtras.push({
        name: "贴片",
        n: adCount,
        total: sampleUsed || 5,
        pct: Math.round((adCount / (sampleUsed || 5)) * 100),
        tone: "purple",
      });
    }

    var sentiment = audience.sentiment || {};
    var pos = sentiment.positive != null ? Number(sentiment.positive) : null;
    var neu = sentiment.neutral != null ? Number(sentiment.neutral) : null;
    var neg = sentiment.negative != null ? Number(sentiment.negative) : null;

    var showCommercial = false;
    var commercial = null;
    if (commercialRaw && typeof commercialRaw === "object") {
      var excerpts = commercialRaw.excerpts || [];
      var cRate = commercialRaw.commercial_nonnull_rate;
      var cAd = commercialRaw.ad_overlay_count || 0;
      var cCta = commercialRaw.cta_purchase_rate;
      var hasSignal =
        (excerpts && excerpts.length > 0) ||
        (cRate != null && Number(cRate) > 0) ||
        cAd > 0 ||
        (cCta != null && Number(cCta) > 0);
      if (hasSignal) {
        showCommercial = true;
        var denom = sampleUsed || 5;
        commercial = {
          nonnullDisplay:
            cRate != null
              ? Math.round(Number(cRate) * denom) + "/" + denom
              : "—",
          adDisplay: cAd + "/" + denom,
          ctaDisplay:
            cCta != null
              ? Math.round(Number(cCta) * denom) + "/" + denom
              : "—",
          excerpts: excerpts.map(function (ex) {
            return {
              videoId: ex.video_id || "",
              text: ex.text || "",
              kind: ex.kind || "commercial",
            };
          }),
        };
      }
    }

    var doneVideos = videos.filter(function (v) {
      var ps = v.pipeline_status;
      return ps === "completed" || ps === "degraded" || ps === "failed" || ps === "cache_hit";
    }).length;
    var sampleProgress = {
      done: doneVideos,
      total: Math.max(videos.length, sample.video_ids ? sample.video_ids.length : 0, sampleUsed || 0, 5),
    };

    var view = {
      schemaVersion: result.schema_version || "",
      resultVersion: result.result_version || "",
      is_fixture: result.is_fixture === true,
      fixtureNote: result.fixture_note || "",
      channel: {
        id: channel.channel_id || "",
        title: channel.title || "频道",
        handle: channel.handle || "",
        avatarText: (channel.title || channel.handle || "频").trim().charAt(0) || "频",
        subscriberDisplay: formatCount(channel.subscriber_count),
        viewDisplay: formatCount(channel.view_count),
        videoCountDisplay: channel.video_count != null ? formatCount(channel.video_count) : "—",
        canonicalUrl: channel.canonical_url || "",
      },
      sample: {
        windowSize: sample.window_size || 50,
        recentCount: sample.recent_count != null ? sample.recent_count : 0,
        hotCount: sample.hot_count != null ? sample.hot_count : 0,
        videoIds: sample.video_ids || [],
        gapNotes: sample.gap_notes || [],
        metaNote:
          "近 " +
          (sample.recent_count != null ? sample.recent_count : 0) +
          " + 热 " +
          (sample.hot_count != null ? sample.hot_count : 0) +
          " · 去重后 " +
          (sample.video_ids ? sample.video_ids.length : videos.length) +
          " 条",
      },
      health: {
        statuses: healthStatuses,
        notes: health.notes || [],
        contentOk: contentOk,
        contentTotal: contentTotal,
        commentsOk: commentsOk,
        commentsTotal: commentsTotal,
      },
      metrics: {
        sampleCountUsed: sampleUsed,
        avgViewDisplay: formatCount(metrics.avg_view_count),
        avgLikeDisplay: formatCount(metrics.avg_like_count),
        avgCommentDisplay: formatCount(metrics.avg_comment_count),
        avgDurationDisplay: formatDuration(metrics.avg_duration_seconds),
        avgErDisplay: formatRate(metrics.avg_engagement_rate),
        channelSubDisplay: formatCount(
          metrics.channel_subscriber_count != null
            ? metrics.channel_subscriber_count
            : channel.subscriber_count
        ),
        channelViewDisplay: formatCount(
          metrics.channel_view_count != null
            ? metrics.channel_view_count
            : channel.view_count
        ),
        channelVideoCountDisplay: formatCount(
          metrics.channel_video_count != null
            ? metrics.channel_video_count
            : channel.video_count
        ),
      },
      recentVsHot: {
        recent: {
          videoCount: (rvh.recent && rvh.recent.video_count) || 0,
          typeSummary: (rvh.recent && rvh.recent.type_summary) || "—",
          toneSummary: (rvh.recent && rvh.recent.tone_summary) || "—",
          avgViewDisplay: formatCount(rvh.recent && rvh.recent.avg_view_count),
          sentimentSummary: (rvh.recent && rvh.recent.sentiment_summary) || "—",
          incomplete: !!(rvh.recent && rvh.recent.incomplete),
        },
        hot: {
          videoCount: (rvh.hot && rvh.hot.video_count) || 0,
          typeSummary: (rvh.hot && rvh.hot.type_summary) || "—",
          toneSummary: (rvh.hot && rvh.hot.tone_summary) || "—",
          avgViewDisplay: formatCount(rvh.hot && rvh.hot.avg_view_count),
          sentimentSummary: (rvh.hot && rvh.hot.sentiment_summary) || "—",
          incomplete: !!(rvh.hot && rvh.hot.incomplete),
        },
      },
      contentHabits: {
        typeBars: typeBars,
        toneBars: toneBars,
        extras: habitExtras,
        theses: (content.theses || []).map(function (th) {
          return {
            videoId: th.video_id || "",
            cohort: th.cohort || "recent",
            text: th.text || "",
            mark: th.cohort === "hot" ? "热" : "近",
          };
        }),
      },
      audienceHabits: {
        pos: pos,
        neu: neu,
        neg: neg,
        posDisplay: formatPct(pos),
        neuDisplay: formatPct(neu),
        negDisplay: formatPct(neg),
        weightedVideoCount: audience.weighted_video_count != null ? audience.weighted_video_count : null,
        themes: (audience.themes || []).map(function (t) {
          return {
            label: t.label || "",
            videoCount: t.video_count != null ? t.video_count : null,
          };
        }),
        reactionTypes: (audience.reaction_types || []).map(function (r) {
          return {
            label: r.label || "",
            shareDisplay: r.share != null ? formatRate(r.share) : "",
          };
        }),
        quotes: (audience.quotes || []).map(function (q) {
          return {
            polarity: q.polarity || "",
            text: q.text || "",
            likesDisplay: formatCount(q.likes),
            videoId: q.video_id || "",
            videoTitle: q.video_title || "",
          };
        }),
      },
      showCommercial: showCommercial,
      commercial: commercial,
      alignment: {
        mismatchCount: alignment.mismatch_count != null ? alignment.mismatch_count : 0,
        mismatchTotal: alignment.mismatch_total != null ? alignment.mismatch_total : sampleUsed || 0,
        contrastSentences: alignment.contrast_sentences || [],
        humanReviewItems: alignment.human_review_items || [],
        highLikeNegatives: alignment.high_like_negatives || [],
      },
      videos: videos.map(function (v) {
        var pipe = pipelineLabel(v.pipeline_status, v.notes);
        var sentPos = v.sentiment_positive;
        return {
          videoId: v.video_id || "",
          cohort: v.cohort || "recent",
          cohortLabel: v.cohort === "hot" ? "热" : "近",
          title: v.title || v.video_id || "未命名",
          pipelineStatus: v.pipeline_status || "",
          pipeline: pipe,
          viewDisplay: formatCount(v.view_count),
          videoType: v.video_type || "—",
          tone: v.tone || "—",
          sentimentDisplay: sentPos == null ? "—" : "正 " + Math.round(Number(sentPos)) + "%",
          childTaskId: v.child_task_id || null,
          notes: v.notes || [],
          pending:
            v.pipeline_status === "queued" ||
            v.pipeline_status === "pending" ||
            v.pipeline_status === "analyzing" ||
            v.pipeline_status === "running" ||
            !v.pipeline_status,
        };
      }),
      ops: {
        verdict: ops.verdict == null ? null : ops.verdict,
        note: ops.note == null ? "" : String(ops.note),
        updatedAt: ops.updated_at || null,
      },
      sampleProgress: sampleProgress,
    };

    return view;
  }

  var api = {
    adaptChannelBrief: adaptChannelBrief,
    formatCount: formatCount,
    formatDuration: formatDuration,
    formatRate: formatRate,
  };
  global.adaptChannelBrief = adaptChannelBrief;
  global.ChannelAdapter = api;
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
