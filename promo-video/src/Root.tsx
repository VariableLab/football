import { Composition } from "remotion";
import { MarketingBanner } from "./scenes/MarketingBanner";
import { ArchitectureDeepDive } from "./scenes/ArchitectureDeepDive";
import { ComparisonCard } from "./scenes/ComparisonCard";

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
      <Composition
        id="ArchitectureDeepDive"
        component={ArchitectureDeepDive}
        durationInFrames={300}
        fps={30}
        width={1920}
        height={1080}
      />
      <Composition
        id="ComparisonCard"
        component={ComparisonCard}
        durationInFrames={200}
        fps={30}
        width={1920}
        height={1080}
      />
    </>
  );
};
