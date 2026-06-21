import { useCurrentFrame, interpolate, Easing } from "remotion";

const BG = "#0f0f0f";
const ACCENT = "#c8a86e";
const TEXT = "#e8e4de";
const MUTED = "#9e9488";
const CARD_BG = "rgba(255,255,255,0.03)";

const stats = [
  { value: "31,402", label: "历史比赛", sub: "46个联赛/锦标赛" },
  { value: "462", label: "球队", sub: "自动发现+人工录入" },
  { value: "157K+", label: "预测", sub: "覆盖5种玩法" },
  { value: "56.6%", label: "回测准确率", sub: "SPF方向预测" },
];

const features = [
  "3层融合建模",
  "概率校准",
  "Kelly仓位优化",
  "EV正期望筛选",
  "赛前快照锁定",
  "全开源可复现",
];

export const MarketingBanner: React.FC<{ variant?: "twitter" | "linkedin" | "github" }> = ({ variant = "github" }) => {
  const frame = useCurrentFrame();
  const isWide = variant === "twitter" || variant === "linkedin";

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        backgroundColor: BG,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: "'Helvetica Neue', Arial, sans-serif",
        padding: isWide ? 40 : 60,
        position: "relative",
        overflow: "hidden",
      }}
    >
      {/* Background grid pattern */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage: `
            linear-gradient(rgba(200,168,110,0.03) 1px, transparent 1px),
            linear-gradient(90deg, rgba(200,168,110,0.03) 1px, transparent 1px)
          `,
          backgroundSize: "40px 40px",
        }}
      />

      {/* Glow effect */}
      <div
        style={{
          position: "absolute",
          top: "20%",
          left: "50%",
          transform: "translateX(-50%)",
          width: 400,
          height: 400,
          borderRadius: "50%",
          background: "radial-gradient(circle, rgba(200,168,110,0.08) 0%, transparent 70%)",
          pointerEvents: "none",
        }}
      />

      {/* Top badge */}
      <div
        style={{
          opacity: interpolate(frame, [0, 20], [0, 1], { easing: Easing.ease, extrapolateLeft: "clamp" }),
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 24,
          zIndex: 1,
        }}
      >
        <span style={{ color: ACCENT, fontSize: 11, letterSpacing: "0.2em", textTransform: "uppercase", fontWeight: 600 }}>
          {isWide ? "OPEN SOURCE PROJECT" : "FOOTBALL ANALYTICS"}
        </span>
        <span style={{ width: 6, height: 6, borderRadius: "50%", backgroundColor: ACCENT, opacity: 0.6 }} />
        <span style={{ color: MUTED, fontSize: 11, letterSpacing: "0.15em", fontWeight: 400 }}>
          CC BY-NC-SA 4.0
        </span>
      </div>

      {/* Main title */}
      <div
        style={{
          opacity: interpolate(frame, [10, 40], [0, 1], { easing: Easing.bezier(0.16, 1, 0.3, 1), extrapolateLeft: "clamp" }),
          transform: `translateY(${interpolate(frame, [10, 40], [20, 0], { easing: Easing.bezier(0.16, 1, 0.3, 1), extrapolateLeft: "clamp" })}px)`,
          textAlign: "center",
          zIndex: 1,
          marginBottom: 8,
        }}
      >
        <span style={{ fontSize: isWide ? 56 : 64, fontWeight: 700, color: TEXT, letterSpacing: "-0.02em" }}>
          WC
        </span>
        <span style={{ fontSize: isWide ? 56 : 64, fontWeight: 300, color: ACCENT, letterSpacing: "-0.01em" }}>
          Analytics
        </span>
      </div>

      {/* Subtitle */}
      <div
        style={{
          opacity: interpolate(frame, [40, 60], [0, 1], { easing: Easing.bezier(0.16, 1, 0.3, 1), extrapolateLeft: "clamp" }),
          transform: `translateY(${interpolate(frame, [40, 60], [15, 0], { easing: Easing.bezier(0.16, 1, 0.3, 1), extrapolateLeft: "clamp" })}px)`,
          fontSize: isWide ? 18 : 20,
          color: MUTED,
          fontWeight: 300,
          letterSpacing: "0.08em",
          textAlign: "center",
          zIndex: 1,
          marginBottom: 36,
        }}
      >
        开源足球概率校准研究框架
      </div>

      {/* Divider line */}
      <div
        style={{
          width: 120,
          height: 1,
          backgroundColor: ACCENT,
          opacity: interpolate(frame, [60, 80], [0, 0.4], { easing: Easing.ease, extrapolateLeft: "clamp" }),
          marginBottom: 36,
          zIndex: 1,
        }}
      />

      {/* Stats row */}
      <div
        style={{
          display: "flex",
          gap: isWide ? 40 : 48,
          marginBottom: 36,
          zIndex: 1,
        }}
      >
        {stats.map((s, i) => {
          const delay = i * 25;
          return (
            <div
              key={s.label}
              style={{
                opacity: interpolate(frame, [delay + 20, delay + 45], [0, 1], {
                  easing: Easing.bezier(0.16, 1, 0.3, 1),
                  extrapolateLeft: "clamp",
                }),
                transform: `translateY(${interpolate(frame, [delay + 20, delay + 45], [15, 0], {
                  easing: Easing.bezier(0.16, 1, 0.3, 1),
                  extrapolateLeft: "clamp",
                })}px)`,
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: isWide ? 28 : 32, fontWeight: 700, color: TEXT, lineHeight: 1.1, marginBottom: 4 }}>
                {s.value}
              </div>
              <div style={{ fontSize: 13, color: ACCENT, fontWeight: 500, marginBottom: 2 }}>{s.label}</div>
              <div style={{ fontSize: 11, color: MUTED, fontWeight: 300 }}>{s.sub}</div>
            </div>
          );
        })}
      </div>

      {/* Feature pills */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 10,
          justifyContent: "center",
          marginBottom: 36,
          zIndex: 1,
        }}
      >
        {features.map((f, i) => {
          const delay = i * 20 + 100;
          return (
            <div
              key={f}
              style={{
                opacity: interpolate(frame, [delay, delay + 20], [0, 1], {
                  easing: Easing.ease,
                  extrapolateLeft: "clamp",
                }),
                backgroundColor: CARD_BG,
                border: "1px solid rgba(200,168,110,0.15)",
                borderRadius: 20,
                padding: "6px 16px",
                fontSize: 12,
                color: MUTED,
                fontWeight: 400,
                letterSpacing: "0.03em",
              }}
            >
              {f}
            </div>
          );
        })}
      </div>

      {/* URLs */}
      <div
        style={{
          display: "flex",
          gap: 32,
          zIndex: 1,
        }}
      >
        {[
          { url: "football.nett.to", label: "在线演示" },
          { url: "github.com/VariableLab/football", label: "GitHub" },
        ].map((u, i) => {
          const delay = 180 + i * 20;
          return (
            <div
              key={u.url}
              style={{
                opacity: interpolate(frame, [delay, delay + 25], [0, 1], {
                  easing: Easing.bezier(0.16, 1, 0.3, 1),
                  extrapolateLeft: "clamp",
                }),
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: isWide ? 14 : 16, color: ACCENT, fontWeight: 500, marginBottom: 2 }}>
                {u.url}
              </div>
              <div style={{ fontSize: 11, color: MUTED, fontWeight: 300 }}>{u.label}</div>
            </div>
          );
        })}
      </div>

      {/* Bottom tag */}
      <div
        style={{
          position: "absolute",
          bottom: 20,
          fontSize: 10,
          color: "rgba(255,255,255,0.15)",
          letterSpacing: "0.1em",
          zIndex: 1,
        }}
      >
        {isWide ? "Built for Academic Research · Not a Betting Tool" : "ACADEMIC RESEARCH · PROBABILITY CALIBRATION · OPEN SOURCE"}
      </div>
    </div>
  );
};
