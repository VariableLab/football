import { useCurrentFrame, interpolate, Easing } from "remotion";

const BG = "#0a0a0a";
const ACCENT = "#c8a86e";
const TEXT = "#e8e4de";
const MUTED = "#9e9488";
const GREEN = "#6b9e6b";
const RED = "#9e6b6b";
const CARD_BG = "rgba(255,255,255,0.02)";
const BORDER = "rgba(200,168,110,0.12)";

export const ComparisonCard: React.FC = () => {
  const frame = useCurrentFrame();

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
        padding: 50,
        position: "relative",
      }}
    >
      {/* Header */}
      <div
        style={{
          opacity: interpolate(frame, [0, 20], [0, 1], { easing: Easing.ease, extrapolateLeft: "clamp" }),
          textAlign: "center",
          marginBottom: 48,
        }}
      >
        <div style={{ fontSize: 12, color: ACCENT, letterSpacing: "0.3em", textTransform: "uppercase", fontWeight: 500, marginBottom: 12 }}>
          Why WC Analytics
        </div>
        <div style={{ fontSize: 32, fontWeight: 700, color: TEXT }}>
          从「猜结果」到「校准概率」
        </div>
      </div>

      {/* Two columns */}
      <div style={{ display: "flex", gap: 40, maxWidth: 900, width: "100%" }}>
        {/* Before */}
        <div
          style={{
            flex: 1,
            opacity: interpolate(frame, [20, 50], [0, 1], { easing: Easing.bezier(0.16, 1, 0.3, 1), extrapolateLeft: "clamp" }),
            backgroundColor: CARD_BG,
            border: `1px solid rgba(158,107,107,0.2)`,
            borderRadius: 16,
            padding: "32px 28px",
          }}
        >
          <div style={{ fontSize: 13, color: RED, letterSpacing: "0.2em", textTransform: "uppercase", fontWeight: 600, marginBottom: 20 }}>
            传统预测
          </div>
          {[
            "❌ 只看输赢结果",
            "❌ 无概率校准",
            "❌ 无法复现",
            "❌ 黑箱模型",
            "❌ 过拟合风险高",
            "❌ 缺乏回测标准",
          ].map((item, i) => (
            <div
              key={item}
              style={{
                opacity: interpolate(frame, [50 + i * 10, 65 + i * 10], [0, 1], {
                  easing: Easing.ease,
                  extrapolateLeft: "clamp",
                }),
                fontSize: 15,
                color: MUTED,
                fontWeight: 300,
                padding: "8px 0",
                borderBottom: i < 5 ? "1px solid rgba(255,255,255,0.04)" : "none",
              }}
            >
              {item}
            </div>
          ))}
        </div>

        {/* Arrow */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            opacity: interpolate(frame, [80, 100], [0, 1], { easing: Easing.ease, extrapolateLeft: "clamp" }),
          }}
        >
          <div style={{ fontSize: 36, color: ACCENT }}>→</div>
        </div>

        {/* After */}
        <div
          style={{
            flex: 1,
            opacity: interpolate(frame, [40, 70], [0, 1], { easing: Easing.bezier(0.16, 1, 0.3, 1), extrapolateLeft: "clamp" }),
            backgroundColor: CARD_BG,
            border: `1px solid rgba(107,158,107,0.2)`,
            borderRadius: 16,
            padding: "32px 28px",
          }}
        >
          <div style={{ fontSize: 13, color: GREEN, letterSpacing: "0.2em", textTransform: "uppercase", fontWeight: 600, marginBottom: 20 }}>
            WC Analytics
          </div>
          {[
            "✅ 输出校准概率",
            "✅ Platt/Isotonic 校准",
            "✅ 全开源可复现",
            "✅ 可解释特征权重",
            "✅ 时序前向验证",
            "✅ RPS + Brier Score",
          ].map((item, i) => (
            <div
              key={item}
              style={{
                opacity: interpolate(frame, [70 + i * 10, 85 + i * 10], [0, 1], {
                  easing: Easing.ease,
                  extrapolateLeft: "clamp",
                }),
                fontSize: 15,
                color: TEXT,
                fontWeight: 300,
                padding: "8px 0",
                borderBottom: i < 5 ? "1px solid rgba(255,255,255,0.04)" : "none",
              }}
            >
              {item}
            </div>
          ))}
        </div>
      </div>

      {/* Bottom CTA */}
      <div
        style={{
          marginTop: 48,
          opacity: interpolate(frame, [140, 160], [0, 1], { easing: Easing.ease, extrapolateLeft: "clamp" }),
          textAlign: "center",
        }}
      >
        <div style={{ fontSize: 20, color: ACCENT, fontWeight: 600, marginBottom: 8 }}>
          football.nett.to
        </div>
        <div style={{ fontSize: 13, color: MUTED }}>
          github.com/VariableLab/football
        </div>
      </div>
    </div>
  );
};
