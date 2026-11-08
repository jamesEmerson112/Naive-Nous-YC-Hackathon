import {Composition} from 'remotion';
import {Video} from './Video';
import {FPS, HEIGHT, TOTAL_SECONDS, WIDTH} from './timeline';

export const RemotionRoot: React.FC = () => (
  <Composition
    id="HardwareBuddy"
    component={Video}
    durationInFrames={Math.round(TOTAL_SECONDS * FPS)}
    fps={FPS}
    width={WIDTH}
    height={HEIGHT}
  />
);
