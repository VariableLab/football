import { useCurrentFrame, interpolate, Easing } from "remotion";

const BG = "#1a1a1a";
const ACCENT = "#c8a86e";
const TEXT = "#e8e4de";
const MUTED = "#9e9488";

const stats = [
  { value: "31,402", label: "Matches", sub: "46 leagues & tournaments" },
  { value: "462", label: "Teams", sub: "auto-discovered" },
  { value: "157,030", label: "Predictions", sub: "5 play types" },
];

export const Scale: React.FC = () => {
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
        At Scale
      </div>

      <div style={{ display: "flex", gap: 60, alignItems: "flex-start" }}>
        {stats.map((s, i) => {
          const delay = i * 40;
          const itemOpacity = interpolate(
            frame,
            [delay, delay + 25],
            [0, 1],
            {
              easing: Easing.bezier(0.16, 1, 0.3, 1),
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }
          );
          const itemY = interpolate(
            frame,
            [delay, delay + 25],
            [30, 0],
            {
              easing: Easing.bezier(0.16, 1, 0.3, 1),
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }
          );

          const countLen = s.value.length;
          const fontSize = countLen > 5 ? 64 : countLen > 3 ? 72 : 80;

          return (
            <div
              key={s.label}
              style={{
                opacity: itemOpacity,
                transform: `translateY(${itemY}px)`,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                textAlign: "center",
              }}
            >
              <span
                style={{
                  fontSize,
                  fontWeight: 700,
                  color: TEXT,
                  fontFamily: "'Helvetica Neue', Arial, sans-serif",
                  lineHeight: 1,
                  marginBottom: 12,
                }}
              >
                {s.value}
              </span>
              <span
                style={{
                  fontSize: 18,
                  color: ACCENT,
                  fontWeight: 500,
                  fontFamily: "'Helvetica Neue', Arial, sans-serif",
                  marginBottom: 6,
                }}
              >
                {s.label}
              </span>
              <span
                style={{
                  fontSize: 13,
                  color: MUTED,
                  fontWeight: 300,
                  fontFamily: "'Helvetica Neue', Arial, sans-serif",
                }}
              >
                {s.sub}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
};
