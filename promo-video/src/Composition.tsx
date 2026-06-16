import { Audio, staticFile } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { Title } from "./scenes/Title";
import { Scale } from "./scenes/Scale";
import { Architecture } from "./scenes/Architecture";
import { Performance } from "./scenes/Performance";
import { OpenSource } from "./scenes/OpenSource";
import { CTA } from "./scenes/CTA";

const TRANSITION_FRAMES = 15;

const S1 = 150;
const S2 = 180;
const S3 = 210;
const S4 = 180;
const S5 = 150;
const S6 = 105;

const TOTAL =
  S1 + S2 + S3 + S4 + S5 + S6 - TRANSITION_FRAMES * 5;

const timing = linearTiming({ durationInFrames: TRANSITION_FRAMES });

export const MyComposition: React.FC = () => {
  return (
    <>
      <Audio src={staticFile("voiceover_en.wav")} />
      <TransitionSeries>
        <TransitionSeries.Sequence durationInFrames={S1}>
          <Title />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={fade()} timing={timing} />
        <TransitionSeries.Sequence durationInFrames={S2}>
          <Scale />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={fade()} timing={timing} />
        <TransitionSeries.Sequence durationInFrames={S3}>
          <Architecture />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={fade()} timing={timing} />
        <TransitionSeries.Sequence durationInFrames={S4}>
          <Performance />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={fade()} timing={timing} />
        <TransitionSeries.Sequence durationInFrames={S5}>
          <OpenSource />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition presentation={fade()} timing={timing} />
        <TransitionSeries.Sequence durationInFrames={S6}>
          <CTA />
        </TransitionSeries.Sequence>
      </TransitionSeries>
    </>
  );
};

export const totalDurationInFrames = TOTAL;
