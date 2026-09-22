/**
 * AnalysisResult (API contract) → Demo renderResults shape.
 * Map health: degraded→warn, failed→bad.
 */
(function (global) {
  const KIND_KEY = { "开场": "open", "展开": "develop", "收束": "close" };

  function mapLevel(level) {
    if (level === "degraded") return "warn";
    if (level === "failed") return "bad";
    return "ok";
  }

  function adapt(result) {
    if (!result) return null;
    const ca = result.content_analysis || {};
    const ka = result.comment_analysis || {};
    const health = result.health || {};
    const metrics = result.metrics || {};
    const contrast = result.contrast_summary || {};
    const video = result.video || {};
    const segs = (ca.segments || []).map((seg) => ({
      key: KIND_KEY[seg.kind] || "develop",
      name: seg.kind,
      conf: Number(seg.confidence || 0),
      text: seg.summary || "",
      ctaType: seg.cta_type || null,
      adOverlay: seg.ad_overlay || null,
      quotes: (seg.captions || []).map((c) => ({ ts: c.start, t: c.text })),
    }));
    const sentiment = ka.sentiment || { positive_pct: 0, neutral_pct: 0, negative_pct: 0 };
    const reactions = (ka.reaction_types || []).map((r) => ({
      name: r.display_label || r.type,
      n: r.count,
      comments: r.examples || [],
    }));
    const themes = (ka.open_themes || []).map((t) => ({
      name: t.name,
      comments: t.examples || [],
    }));
    const cc = ca.content_conclusion || {};
    const kc = ka.comment_conclusion || {};
    const posQ = ka.top_positive_quote || { text: "—", like_count_display: "—" };
    const negQ = ka.top_negative_quote || { text: "—", like_count_display: "—" };
    const sentEx = ka.sentiment_examples || { "正面": [], "中性": [], "负面": [] };
    const isFixture = result.is_fixture === true;
    const isPreview = result.is_preview === true;

    return {
      id: video.video_id,
      label: (cc.video_types || []).join(" · ") || "视频",
      title: video.title || "",
      channel: video.channel_name || "",
      is_fixture: isFixture,
      is_preview: isPreview,
      health: {
        overall: health.overall_label || health.overall_level || "",
        source: {
          text: (health.source && health.source.label) || "",
          level: mapLevel(health.source && health.source.level),
        },
        llm: {
          text: (health.llm && health.llm.label) || "",
          level: mapLevel(health.llm && health.llm.level),
        },
        notes: health.notes || [],
      },
      contrast: {
        content: contrast.content_says || "",
        comments: contrast.comments_reply || "",
        sentence: contrast.contrast_sentence || "",
      },
      tags: contrast.freshness_tags || [],
      fetched: contrast.fetched_at_label || "",
      metrics: [
        { k: "播放量", v: metrics.view_count_display || "0", u: "次" },
        { k: "点赞", v: metrics.like_count_display || "0", u: "次" },
        { k: "评论", v: metrics.comment_count_display || "0", u: metrics.comment_count_unit || "条" },
        { k: "发布距今", v: metrics.published_ago_display || "—", u: metrics.published_ago_note || "" },
        { k: "时长", v: metrics.duration_display || "—", u: metrics.duration_note || "" },
        { k: "受众互动比", v: metrics.engagement_ratio_display || "—", u: metrics.engagement_ratio_note || "" },
      ],
      segs,
      contentConclusion: {
        types: cc.video_types || [],
        thesis: cc.thesis || "",
        tone: cc.tone_description || cc.tone || "",
        commercial: cc.commercial == null ? null : cc.commercial,
        confidence: String(cc.confidence != null ? cc.confidence : ""),
        coverage: cc.coverage_label || "",
      },
      sentiment: {
        pos: sentiment.positive_pct || 0,
        neu: sentiment.neutral_pct || 0,
        neg: sentiment.negative_pct || 0,
      },
      reactions,
      themes,
      posQuote: { t: posQ.text || "—", like: posQ.like_count_display || "—" },
      negQuote: { t: negQ.text || "—", like: negQ.like_count_display || "—" },
      sentComments: {
        pos: sentEx["正面"] || [],
        neu: sentEx["中性"] || [],
        neg: sentEx["负面"] || [],
      },
      lowConf: (ka.low_confidence_items || []).map((x) => ({
        t: x.text,
        sc: String(x.confidence),
      })),
      review: (ka.human_review_items || []).map((x) => ({
        t: x.text,
        sc: x.status || "待确认",
      })),
      noise: (ka.noise && ka.noise.label) || "",
      commentConclusion: {
        doing: kc.doing || "",
        themes: kc.themes || "",
        match: kc.alignment || "",
        confidence: String(kc.confidence != null ? kc.confidence : ""),
        sample: kc.sample_label || "",
      },
    };
  }

  global.adaptAnalysisResult = adapt;
})(typeof window !== "undefined" ? window : globalThis);
