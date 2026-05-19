import { useCurrentFrame, interpolate, Easing } from "remotion";

const BG = "#1a1a1a";
const ACCENT = "#c8a86e";
const TEXT = "#e8e4de";
const MUTED = "#9e9488";

const badges = [
  "Python 3.10+",
  "FastAPI",
  "Logistic Regression",
  "Residual NN",
  "Elo",
  "Poisson (Dixon-Coles)",
  "31K+ matches",
  "Open Source",
];

export const OpenSource: React.FC = () => {
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
          marginBottom: 12,
        }}
      >
        Fully Open Source
      </div>

      <div
        style={{
          fontSize: 16,
          color: MUTED,
          fontFamily: "'Helvetica Neue', Arial, sans-serif",
          fontWeight: 300,
          marginBottom: 36,
        }}
      >
        github.com/VariableLab/football
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 10,
          justifyContent: "center",
          maxWidth: 600,
          marginBottom: 40,
        }}
      >
        {badges.map((b, i) => {
          const delay = i * 12;
          const opacity = interpolate(
            frame,
            [delay, delay + 15],
            [0, 1],
            {
              easing: Easing.bezier(0.16, 1, 0.3, 1),
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }
          );
          const scale = interpolate(
            frame,
            [delay, delay + 15],
            [0.85, 1],
            {
              easing: Easing.bezier(0.34, 1.56, 0.64, 1),
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }
          );
          return (
            <div
              key={b}
              style={{
                opacity,
                transform: `scale(${scale})`,
                padding: "8px 16px",
                backgroundColor: "rgba(255,255,255,0.06)",
                borderRadius: 20,
                border: "1px solid rgba(255,255,255,0.08)",
                fontSize: 13,
                color: MUTED,
                fontFamily: "'Helvetica Neue', Arial, sans-serif",
              }}
            >
              {b}
            </div>
          );
        })}
      </div>

      <div
        style={{
          opacity: interpolate(frame, [90, 115], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          }),
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 8,
        }}
      >
        <div
          style={{
            width: 40,
            height: 1,
            backgroundColor: ACCENT,
            marginBottom: 8,
          }}
        />
        <div
          style={{
            fontSize: 14,
            color: MUTED,
            fontFamily: "'Helvetica Neue', Arial, sans-serif",
            textAlign: "center",
            maxWidth: 500,
            fontStyle: "italic",
          }}
        >
          Academic Research Tool · Pre-match snapshots · Not betting advice
        </div>
      </div>
    </div>
  );
};
