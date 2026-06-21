import { Composition } from "remotion";
import { MarketingBanner } from "./scenes/MarketingBanner";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="MarketingSquare"
        component={MarketingBanner}
        durationInFrames={270}
        fps={30}
        width={1080}
        height={1080}
      />
      <Composition
        id="MarketingWide"
        component={() => <MarketingBanner variant="twitter" />}
        durationInFrames={270}
        fps={30}
        width={1920}
        height={1080}
      />
      <Composition
        id="MarketingLinkedIn"
        component={() => <MarketingBanner variant="linkedin" />}
        durationInFrames={270}
        fps={30}
        width={1200}
        height={627}
      />
    </>
  );
};
