import { Composition } from "remotion";
import { MyComposition, totalDurationInFrames } from "./Composition";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="WCAnalytics"
        component={MyComposition}
        durationInFrames={totalDurationInFrames}
        fps={30}
        width={1280}
        height={720}
      />
    </>
  );
};
