import { useCurrentFrame, interpolate, Easing } from "remotion";

const BG = "#0a0a0a";
const ACCENT = "#c8a86e";
const TEXT = "#e8e4de";
const MUTED = "#9e9488";
const CARD_BG = "rgba(255,255,255,0.02)";
const BORDER = "rgba(200,168,110,0.12)";

const layers = [
  {
    num: "LAYER 1",
    title: "Feature Generation",
    desc: "6个独立特征模型并行提取",
    items: ["Elo 评分基线", "Poisson(Dixon-Coles)", "市场赔率去水", "8项调整因子", "FormMarkov 时序", "H2H 对战特征"],
    color: "#5b8c8c",
  },
  {
    num: "LAYER 2",
    title: "Logistic Fusion",
    desc: "43维特征 → 多项式逻辑回归",
    items: ["L1 正则化自动特征选择", "交叉熵损失优化", "系数即特征贡献度", "自然输出校准概率", "支持联赛分层训练"],
    color: ACCENT,
  },
  {
    num: "LAYER 3",
    title: "Residual NN",
    desc: "修正 LR 系统偏差",
    items: ["3层 MLP 残差网络", "学习 LR 残差分布", "端到端微调", "提升校准度"],
    color: "#9e7bb5",
  },
];

export const ArchitectureDeepDive: React.FC = () => {
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
        overflow: "hidden",
      }}
    >
      {/* Background */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage: `
            radial-gradient(circle at 30% 30%, rgba(200,168,110,0.04) 0%, transparent 50%),
            radial-gradient(circle at 70% 70%, rgba(91,140,140,0.03) 0%, transparent 50%)
          `,
          pointerEvents: "none",
        }}
      />

      {/* Header */}
      <div
        style={{
          opacity: interpolate(frame, [0, 20], [0, 1], { easing: Easing.ease, extrapolateLeft: "clamp" }),
          textAlign: "center",
          marginBottom: 40,
          zIndex: 1,
        }}
      >
        <div style={{ fontSize: 12, color: ACCENT, letterSpacing: "0.3em", textTransform: "uppercase", fontWeight: 500, marginBottom: 12 }}>
          Technical Deep Dive
        </div>
        <div style={{ fontSize: 36, fontWeight: 700, color: TEXT, letterSpacing: "-0.01em" }}>
          3-Layer Fusion Architecture
        </div>
      </div>

      {/* Layers */}
      <div style={{ display: "flex", gap: 24, zIndex: 1, maxWidth: 1000 }}>
        {layers.map((layer, i) => {
          const delay = i * 30;
          return (
            <div
              key={layer.title}
              style={{
                opacity: interpolate(frame, [delay + 10, delay + 40], [0, 1], {
                  easing: Easing.bezier(0.16, 1, 0.3, 1),
                  extrapolateLeft: "clamp",
                }),
                transform: `translateY(${interpolate(frame, [delay + 10, delay + 40], [20, 0], {
                  easing: Easing.bezier(0.16, 1, 0.3, 1),
                  extrapolateLeft: "clamp",
                })}px)`,
                flex: 1,
                backgroundColor: CARD_BG,
                border: `1px solid ${BORDER}`,
                borderRadius: 16,
                padding: "28px 24px",
                display: "flex",
                flexDirection: "column",
              }}
            >
              {/* Layer badge */}
              <div
                style={{
                  fontSize: 10,
                  color: layer.color,
                  letterSpacing: "0.2em",
                  textTransform: "uppercase",
                  fontWeight: 600,
                  marginBottom: 8,
                }}
              >
                {layer.num}
              </div>

              {/* Title */}
              <div style={{ fontSize: 18, fontWeight: 700, color: TEXT, marginBottom: 4 }}>
                {layer.title}
              </div>
              <div style={{ fontSize: 12, color: MUTED, marginBottom: 20, fontStyle: "italic" }}>
                {layer.desc}
              </div>

              {/* Items */}
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {layer.items.map((item, j) => {
                  const itemDelay = delay + j * 10;
                  return (
                    <div
                      key={item}
                      style={{
                        opacity: interpolate(frame, [itemDelay + 20, itemDelay + 35], [0, 1], {
                          easing: Easing.ease,
                          extrapolateLeft: "clamp",
                        }),
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        fontSize: 13,
                        color: MUTED,
                        fontWeight: 300,
                      }}
                    >
                      <span style={{ color: layer.color, fontSize: 6 }}>●</span>
                      {item}
                    </div>
                  );
                })}
              </div>

              {/* Connector arrow at bottom (except last) */}
              {i < layers.length - 1 && (
                <div
                  style={{
                    position: "absolute",
                    right: -14,
                    top: "50%",
                    fontSize: 16,
                    color: MUTED,
                    opacity: interpolate(frame, [delay + 40, delay + 55], [0, 0.5], {
                      easing: Easing.ease,
                      extrapolateLeft: "clamp",
                    }),
                  }}
                >
                  →
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Bottom metrics */}
      <div
        style={{
          display: "flex",
          gap: 40,
          marginTop: 40,
          zIndex: 1,
        }}
      >
        {[
          { value: "43", label: "融合特征数" },
          { value: "31K+", label: "训练样本" },
          { value: "56.6%", label: "最佳回测准确率" },
          { value: "0.185", label: "Brier Score" },
        ].map((m, i) => {
          const delay = 150 + i * 15;
          return (
            <div
              key={m.label}
              style={{
                opacity: interpolate(frame, [delay, delay + 20], [0, 1], {
                  easing: Easing.ease,
                  extrapolateLeft: "clamp",
                }),
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: 24, fontWeight: 700, color: ACCENT, marginBottom: 4 }}>{m.value}</div>
              <div style={{ fontSize: 11, color: MUTED, fontWeight: 300 }}>{m.label}</div>
            </div>
          );
        })}
      </div>

      {/* Footer */}
      <div
        style={{
          position: "absolute",
          bottom: 20,
          fontSize: 10,
          color: "rgba(255,255,255,0.12)",
          letterSpacing: "0.1em",
          zIndex: 1,
        }}
      >
        github.com/VariableLab/football · CC BY-NC-SA 4.0
      </div>
    </div>
  );
};
