import { useCurrentFrame, interpolate, Easing } from "remotion";

const BG = "#1a1a1a";
const ACCENT = "#c8a86e";
const TEXT = "#e8e4de";
const MUTED = "#9e9488";

export const CTA: React.FC = () => {
  const frame = useCurrentFrame();

  const opacity = interpolate(frame, [0, 25], [0, 1], {
    easing: Easing.bezier(0.16, 1, 0.3, 1),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const ctaScale = interpolate(frame, [0, 25], [0.9, 1], {
    easing: Easing.bezier(0.34, 1.56, 0.64, 1),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const urlOpacity = interpolate(frame, [30, 55], [0, 1], {
    easing: Easing.bezier(0.16, 1, 0.3, 1),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const urls = [
    { url: "football.nett.to", label: "Live Demo" },
    { url: "github.com/VariableLab/football", label: "GitHub" },
  ];

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
        fontFamily: "'Helvetica Neue', Arial, sans-serif",
      }}
    >
      <div
        style={{
          opacity,
          transform: `scale(${ctaScale})`,
          textAlign: "center",
        }}
      >
        <div
          style={{
            fontSize: 16,
            color: ACCENT,
            letterSpacing: "0.25em",
            textTransform: "uppercase",
            fontWeight: 500,
            marginBottom: 16,
          }}
        >
          Available Now
        </div>
        <div
          style={{
            fontSize: 36,
            color: TEXT,
            fontWeight: 700,
            marginBottom: 32,
            letterSpacing: "0.03em",
          }}
        >
          WC Analytics
          <span style={{ color: ACCENT }}>.</span>
        </div>
      </div>

      <div
        style={{
          opacity: urlOpacity,
          display: "flex",
          flexDirection: "column",
          gap: 16,
          alignItems: "center",
        }}
      >
        {urls.map((u, i) => {
          const uDelay = i * 15;
          const uOpacity = interpolate(
            frame,
            [30 + uDelay, 50 + uDelay],
            [0, 1],
            {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }
          );
          return (
            <div
              key={u.url}
              style={{
                opacity: uOpacity,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 4,
              }}
            >
              <span
                style={{
                  fontSize: 20,
                  color: ACCENT,
                  fontWeight: 500,
                }}
              >
                {u.url}
              </span>
              <span
                style={{
                  fontSize: 12,
                  color: MUTED,
                }}
              >
                {u.label}
              </span>
            </div>
          );
        })}
      </div>

      <div
        style={{
          opacity: interpolate(frame, [65, 85], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          }),
          marginTop: 40,
          fontSize: 11,
          color: "rgba(255,255,255,0.2)",
          textAlign: "center",
        }}
      >
        Built with Remotion · Open source · MIT License
      </div>
    </div>
  );
};
