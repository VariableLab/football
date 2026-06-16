import { useCurrentFrame, interpolate, Easing } from "remotion";

const BG = "#1a1a1a";
const ACCENT = "#c8a86e";
const TEXT = "#e8e4de";
const MUTED = "#9e9488";

export const Title: React.FC = () => {
  const frame = useCurrentFrame();

  const titleOpacity = interpolate(frame, [0, 30], [0, 1], {
    easing: Easing.bezier(0.16, 1, 0.3, 1),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const titleY = interpolate(frame, [0, 30], [30, 0], {
    easing: Easing.bezier(0.16, 1, 0.3, 1),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const subOpacity = interpolate(frame, [30, 55], [0, 1], {
    easing: Easing.bezier(0.16, 1, 0.3, 1),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const subY = interpolate(frame, [30, 55], [20, 0], {
    easing: Easing.bezier(0.16, 1, 0.3, 1),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const lineScale = interpolate(frame, [40, 70], [0, 1], {
    easing: Easing.bezier(0.22, 1, 0.36, 1),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const dotOpacity = interpolate(frame, [55, 150], [0, 1], {
    easing: Easing.bezier(0.45, 0, 0.55, 1),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const dots = ["Academic Research", "Pre-Match Snapshots", "Fully Reproducible"];

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
        fontFamily: "Georgia, 'Times New Roman', serif",
        padding: 60,
      }}
    >
      <div
        style={{
          opacity: titleOpacity,
          transform: `translateY(${titleY}px)`,
          display: "flex",
          alignItems: "baseline",
          gap: 8,
        }}
      >
        <span
          style={{
            fontSize: 72,
            fontWeight: 700,
            color: TEXT,
            letterSpacing: "0.05em",
          }}
        >
          WC
        </span>
        <span
          style={{
            fontSize: 72,
            fontWeight: 300,
            color: ACCENT,
            letterSpacing: "0.02em",
          }}
        >
          Analytics
        </span>
      </div>

      <div
        style={{
          width: 80,
          height: 2,
          backgroundColor: ACCENT,
          margin: "24px 0",
          transform: `scaleX(${lineScale})`,
          transformOrigin: "center",
        }}
      />

      <div
        style={{
          opacity: subOpacity,
          transform: `translateY(${subY}px)`,
          fontSize: 22,
          color: MUTED,
          fontWeight: 300,
          letterSpacing: "0.15em",
          textTransform: "uppercase",
          fontFamily: "'Helvetica Neue', Arial, sans-serif",
          marginBottom: 40,
        }}
      >
        Football Probability Calibration Framework
      </div>

      <div style={{ display: "flex", gap: 24, opacity: dotOpacity }}>
        {dots.map((d, i) => {
          const delay = i * 20;
          const itemOpacity = interpolate(
            frame,
            [55 + delay, 70 + delay],
            [0, 1],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
          );
          return (
            <div
              key={d}
              style={{
                opacity: itemOpacity,
                display: "flex",
                alignItems: "center",
                gap: 8,
                fontSize: 14,
                color: MUTED,
                fontFamily: "'Helvetica Neue', Arial, sans-serif",
              }}
            >
              <span style={{ color: ACCENT, fontSize: 8 }}>●</span>
              {d}
            </div>
          );
        })}
      </div>
    </div>
  );
};
