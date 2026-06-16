import { useCurrentFrame, interpolate, Easing } from "remotion";

const BG = "#1a1a1a";
const ACCENT = "#c8a86e";
const MUTED = "#9e9488";

const layers = [
  {
    title: "Feature Generation",
    items: ["Elo Rating", "Poisson (Dixon-Coles)", "Market Odds", "Form Markov"],
    color: "#5b8c8c",
  },
  {
    title: "LR Fusion",
    items: ["Logistic Regression (L1)", "43 features → SPF prob"],
    color: ACCENT,
  },
  {
    title: "Residual NN",
    items: ["3-layer MLP", "Corrects systematic bias"],
    color: "#9e7bb5",
  },
];

export const Architecture: React.FC = () => {
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
        padding: 50,
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
          marginBottom: 40,
        }}
      >
        3-Layer Architecture
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 0 }}>
        {layers.map((layer, i) => {
          const delay = i * 50;
          const opacity = interpolate(frame, [delay, delay + 25], [0, 1], {
            easing: Easing.bezier(0.16, 1, 0.3, 1),
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          const y = interpolate(frame, [delay, delay + 25], [20, 0], {
            easing: Easing.bezier(0.16, 1, 0.3, 1),
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });

          const arrowOpacity = interpolate(
            frame,
            [delay + 15, delay + 30],
            [0, 1],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
          );

          return (
            <div key={layer.title} style={{ display: "flex", alignItems: "center" }}>
              <div
                style={{
                  opacity,
                  transform: `translateY(${y}px)`,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  width: 280,
                }}
              >
                <div
                  style={{
                    backgroundColor: `${layer.color}22`,
                    border: `1px solid ${layer.color}55`,
                    borderRadius: 12,
                    padding: "20px 24px",
                    width: "100%",
                  }}
                >
                  <div
                    style={{
                      fontSize: 16,
                      color: layer.color,
                      fontWeight: 600,
                      fontFamily: "'Helvetica Neue', Arial, sans-serif",
                      marginBottom: 16,
                      textAlign: "center",
                    }}
                  >
                    {layer.title}
                  </div>
                  {layer.items.map((item, j) => {
                    const itemDelay = delay + j * 15;
                    const itemOpacity = interpolate(
                      frame,
                      [itemDelay + 25, itemDelay + 40],
                      [0, 1],
                      { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
                    );
                    return (
                      <div
                        key={item}
                        style={{
                          opacity: itemOpacity,
                          fontSize: 13,
                          color: MUTED,
                          fontFamily: "'Helvetica Neue', Arial, sans-serif",
                          padding: "4px 0",
                          textAlign: "center",
                          borderTop: j > 0 ? "1px solid rgba(255,255,255,0.06)" : "none",
                          paddingTop: j > 0 ? 8 : 0,
                          marginTop: j > 0 ? 8 : 0,
                        }}
                      >
                        {item}
                      </div>
                    );
                  })}
                </div>
              </div>

              {i < layers.length - 1 && (
                <div
                  style={{
                    opacity: arrowOpacity,
                    width: 40,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 24,
                    color: MUTED,
                    padding: "0 8px",
                  }}
                >
                  →
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div
        style={{
          marginTop: 36,
          fontSize: 13,
          color: MUTED,
          fontFamily: "'Helvetica Neue', Arial, sans-serif",
          textAlign: "center",
          opacity: interpolate(frame, [180, 210], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          }),
        }}
      >
        Platt Scaling calibration · EV calculation · 4-tier risk filtering · Kelly optimization
      </div>
    </div>
  );
};
