import { useCurrentFrame, interpolate, Easing } from "remotion";

const BG = "#1a1a1a";
const ACCENT = "#c8a86e";
const TEXT = "#e8e4de";
const MUTED = "#9e9488";
const GREEN = "#6b9e6b";

const metrics = [
  { value: 56.6, label: "SPF Accuracy", target: 55, unit: "%", color: ACCENT },
  { value: 0.185, label: "Brier Score", target: 0.19, unit: "", color: GREEN, inverse: true },
  { value: 49.3, label: "Knockout Acc.", target: 45, unit: "%", color: "#5b8c8c" },
];

export const Performance: React.FC = () => {
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
        padding: 60,
      }}
    >
      <div
        style={{
          fontSize: 14,
          color: ACCENT,
          letterSpacing: "0.25em",
          textTransform: "uppercase",
          fontFamily: "'Helvetica Neue', Arial, sans-serif",
          fontWeight: 500,
          marginBottom: 48,
        }}
      >
        Backtest Performance
      </div>

      <div style={{ display: "flex", gap: 50, alignItems: "flex-end" }}>
        {metrics.map((m, i) => {
          const delay = i * 45;
          const opacity = interpolate(frame, [delay, delay + 20], [0, 1], {
            easing: Easing.bezier(0.16, 1, 0.3, 1),
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          const y = interpolate(frame, [delay, delay + 20], [20, 0], {
            easing: Easing.bezier(0.16, 1, 0.3, 1),
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });

          const maxBarHeight = 200;
          const valRatio = m.inverse
            ? 1 - m.value / 0.3
            : m.value / 100;
          const barHeight = interpolate(
            frame,
            [delay + 20, delay + 50],
            [0, Math.max(valRatio * maxBarHeight, 10)],
            {
              easing: Easing.bezier(0.22, 1, 0.36, 1),
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }
          );

          const targetRatio = m.inverse ? 1 - m.target / 0.3 : m.target / 100;
          const targetY = maxBarHeight - targetRatio * maxBarHeight;
          const targetOpacity = interpolate(
            frame,
            [delay + 50, delay + 65],
            [0, 1],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
          );

          return (
            <div
              key={m.label}
              style={{
                opacity,
                transform: `translateY(${y}px)`,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                width: 200,
              }}
            >
              <div
                style={{
                  fontSize: m.value < 1 ? 32 : 44,
                  fontWeight: 700,
                  color: TEXT,
                  fontFamily: "'Helvetica Neue', Arial, sans-serif",
                  lineHeight: 1,
                  marginBottom: 4,
                }}
              >
                {m.value}
                {m.unit}
              </div>
              <div
                style={{
                  fontSize: 14,
                  color: MUTED,
                  fontFamily: "'Helvetica Neue', Arial, sans-serif",
                  marginBottom: 24,
                }}
              >
                {m.label}
              </div>

              <div
                style={{
                  width: 60,
                  height: maxBarHeight,
                  backgroundColor: "rgba(255,255,255,0.05)",
                  borderRadius: 4,
                  position: "relative",
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    position: "absolute",
                    bottom: 0,
                    left: 0,
                    right: 0,
                    height: barHeight,
                    backgroundColor: m.color,
                    borderRadius: 4,
                    opacity: 0.8,
                  }}
                />

                <div
                  style={{
                    position: "absolute",
                    bottom: targetY,
                    left: -8,
                    right: -8,
                    height: 2,
                    backgroundColor: "rgba(255,255,255,0.4)",
                    opacity: targetOpacity,
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      right: -12,
                      top: -10,
                      fontSize: 10,
                      color: "rgba(255,255,255,0.5)",
                      fontFamily: "'Helvetica Neue', Arial, sans-serif",
                      whiteSpace: "nowrap",
                    }}
                  >
                    target {m.target}{m.unit}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div
        style={{
          marginTop: 40,
          fontSize: 12,
          color: MUTED,
          fontFamily: "'Helvetica Neue', Arial, sans-serif",
          textAlign: "center",
          opacity: interpolate(frame, [135, 150], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          }),
        }}
      >
        Pre-match snapshot · Historical backtest on 31K+ matches
      </div>
    </div>
  );
};
