import {AbsoluteFill, Audio, interpolate, OffthreadVideo, Sequence, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Beat, BEATS, FPS, HUD, TOOL_FEED} from './timeline';
import {theme} from './theme';

const sec = (s: number) => Math.round(s * FPS);

const Placeholder: React.FC<{beat: Beat}> = ({beat}) => {
  const frame = useCurrentFrame();
  const fade = interpolate(frame, [0, 8], [0, 1], {extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{background: theme.bg, color: theme.ink, fontFamily: theme.font, opacity: fade, padding: 120, justifyContent: 'center'}}>
      <div style={{fontSize: 28, letterSpacing: 4, textTransform: 'uppercase', color: theme.accent}}>
        Beat {beat.id} · {beat.title} · placeholder
      </div>
      <div style={{fontSize: 54, fontWeight: 700, marginTop: 24, lineHeight: 1.15, maxWidth: 1500}}>{beat.visual}</div>
      <div style={{fontSize: 26, marginTop: 40, color: theme.muted}}>
        {beat.end - beat.start} s · voice: {beat.voice ?? 'none'}
      </div>
    </AbsoluteFill>
  );
};

const Caption: React.FC<{beat: Beat}> = ({beat}) => {
  if (!beat.vo) return null;
  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 56}}>
      <div style={{background: 'rgba(18,18,20,0.78)', color: '#fff', fontFamily: theme.font, fontSize: 30, lineHeight: 1.35, padding: '14px 26px', borderRadius: 14, maxWidth: 1500, textAlign: 'center'}}>
        {beat.voice ? <b style={{color: theme.accentSoft}}>{beat.voice}: </b> : null}
        {beat.vo}
      </div>
    </AbsoluteFill>
  );
};

const SpeedTag: React.FC<{rate: number}> = ({rate}) => (
  <div style={{position: 'absolute', left: 48, top: 40, fontFamily: theme.mono, fontSize: 26, color: '#fff', background: 'rgba(0,0,0,0.55)', padding: '6px 14px', borderRadius: 8}}>
    {rate}× speed
  </div>
);

const ToolFeed: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <div style={{position: 'absolute', right: 48, top: 140, width: 760, display: 'flex', flexDirection: 'column', gap: 14, fontFamily: theme.mono}}>
      {TOOL_FEED.map((t) => {
        const local = frame - sec(t.at);
        if (local < 0) return null;
        const s = spring({frame: local, fps, config: {damping: 200}});
        const ok = t.result.startsWith('✓');
        return (
          <div key={t.name + t.at} style={{transform: `translateX(${(1 - s) * 80}px)`, opacity: s, background: theme.card, border: `2px solid ${ok ? theme.ok : theme.line}`, borderRadius: 14, padding: '12px 18px', display: 'flex', gap: 14, alignItems: 'baseline'}}>
            <span style={{fontSize: 28, fontWeight: 700, color: theme.ink}}>▶ {t.name}</span>
            <span style={{fontSize: 18, color: theme.muted, border: `1px solid ${theme.line}`, borderRadius: 6, padding: '1px 8px'}}>{t.risk}</span>
            <span style={{fontSize: 22, color: ok ? theme.ok : theme.muted, marginLeft: 'auto'}}>{t.result}</span>
          </div>
        );
      })}
    </div>
  );
};

const Hud: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS + HUD.from;
  const online = t >= HUD.onlineAt;
  const touches = t >= HUD.approveAt ? 1 : 0;
  return (
    <div style={{position: 'absolute', right: 48, top: 40, display: 'flex', gap: 14, fontFamily: theme.mono, fontSize: 24}}>
      <div style={{background: online ? theme.ok : theme.bad, color: '#fff', padding: '8px 16px', borderRadius: 999}}>● {online ? 'ONLINE' : 'OFFLINE'}</div>
      <div style={{background: 'rgba(18,18,20,0.78)', color: '#fff', padding: '8px 16px', borderRadius: 999}}>Human touches: {touches}</div>
    </div>
  );
};

const BeatView: React.FC<{beat: Beat}> = ({beat}) => (
  <AbsoluteFill>
    {beat.clip ? (
      <OffthreadVideo src={staticFile(`footage/${beat.clip}`)} startFrom={sec(beat.clipFrom ?? 0)} playbackRate={beat.playbackRate ?? 1} muted={!!beat.voice} />
    ) : (
      <Placeholder beat={beat} />
    )}
    {beat.id === 8 ? <ToolFeed /> : null}
    {beat.playbackRate && beat.playbackRate > 1 ? <SpeedTag rate={beat.playbackRate} /> : null}
    {beat.vo_file ? <Audio src={staticFile(`footage/${beat.vo_file}`)} /> : null}
    <Caption beat={beat} />
  </AbsoluteFill>
);

export const Video: React.FC = () => (
  <AbsoluteFill style={{background: '#000'}}>
    {BEATS.map((b) => (
      <Sequence key={b.id} from={sec(b.start)} durationInFrames={sec(b.end - b.start)} name={`${b.id} ${b.title}`}>
        <BeatView beat={b} />
      </Sequence>
    ))}
    <Sequence from={sec(HUD.from)} durationInFrames={sec(HUD.to - HUD.from)} name="HUD">
      <Hud />
    </Sequence>
  </AbsoluteFill>
);
