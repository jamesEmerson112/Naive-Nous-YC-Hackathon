// The whole cut lives here. Times are seconds from the start of the video.
// Re-cut: change start/end or `clip`, then `npm run render`.
// `clip` is a file under public/footage (a symlink to ~/Movies/naive-demo/raw); null shows a placeholder card.

export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;

export type Beat = {
  id: number;
  start: number;
  end: number;
  title: string;
  voice: string | null;
  vo: string;
  visual: string;
  clip: string | null;
  clipFrom?: number; // seconds into the clip to start
  playbackRate?: number; // speed-up; shown on screen when > 1
  vo_file?: string | null; // e.g. 'vo/vo-08-ishaan.m4a'
};

export const BEATS: Beat[] = [
  {id: 1, start: 0, end: 7, title: 'Downtime', voice: 'Andre', vo: 'The world’s biggest companies lose 1.4 trillion dollars a year to unplanned downtime.', visual: 'Andre scene 1: arm freezes, RUNNING → DOWN, counter to $1.4T', clip: null},
  {id: 2, start: 7, end: 18, title: 'Out of reach', voice: 'Andre', vo: '[Andre’s line: satellites · Guam · short contact window · no internet · millions lost]', visual: 'Andre scene 2: Earth curve, GUAM antenna, contact window, link breaks', clip: null},
  {id: 3, start: 18, end: 23, title: 'Reveal', voice: 'Ishaan', vo: 'So we built Hardware Buddy: a self-healing OS for robotics and manufacturing.', visual: 'Andre scene 3: Buddy chip draws in, blinks, wordmark', clip: null},
  {id: 4, start: 23, end: 30, title: 'Onboarding', voice: 'Kent', vo: 'Naïve agents find hardware companies and set them up autonomously, with coding agents.', visual: 'Andre scene 4: envelope → factory; terminal ticks', clip: null},
  {id: 5, start: 30, end: 39, title: 'Two parts', voice: 'Olir', vo: 'A local model on the device works offline. The cloud syncs when it’s back online.', visual: 'Andre scene 5: local and cloud cards, link drops and returns', clip: null},
  {id: 6, start: 39, end: 42, title: 'Demo bridge', voice: null, vo: '', visual: 'LIVE DEMO · SO-101 arm · ultrasonic · STM32 firmware', clip: null},
  {id: 7, start: 42, end: 46, title: 'The fault', voice: null, vo: '', visual: 'Real arm on firmware v1.7 flails and gives up', clip: null},
  {id: 8, start: 46, end: 68, title: 'It heals itself', voice: 'Ishaan', vo: 'That’s a firmware bug in the distance sensor, and there’s no internet. The local model on the device takes over with heal tools we built. It rolls the firmware back, re-runs the job, checks every joint, then finds the root cause: a 16-bit overflow on line 122. Nobody touched it.', visual: 'Arm + pod left, tool-call feed right, diff card, bullets', clip: null},
  {id: 9, start: 68, end: 85, title: 'When it needs hands', voice: 'Kent', vo: 'Some faults need hands. The healer can’t clear this jam, so a Naïve agent takes the case: diagnoses it, emails a technician what to check, and puts a spare servo in a cart. Our one step: approve.', visual: 'Jammed elbow → Naïve session → verdict → phone email → part card → Approve → cart', clip: null},
  {id: 10, start: 85, end: 93, title: 'Sync', voice: 'Olir', vo: 'Back online, every incident and every fix syncs to the cloud.', visual: 'Run report lands in Romulus; incident timeline', clip: null},
  {id: 11, start: 93, end: 104, title: 'The fleet learns', voice: 'Ishaan', vo: 'Fixes that hold are replayed, tested on a simulated twin, and shipped to every robot. What one robot learns, every robot gets.', visual: 'Fleet → cloud → replay → sim canary → promote → back to all', clip: null},
  {id: 12, start: 104, end: 116, title: 'Closing', voice: 'Andre', vo: 'Software is automating everything. Hardware has to get smarter. That’s Hardware Buddy.', visual: 'Andre scene 9: thesis, Buddy mark, Built on Naïve, team', clip: null},
];

export const TOTAL_SECONDS = BEATS[BEATS.length - 1].end;

// HUD: offline from the fault until the sync beat; the human-touch counter ticks at the Approve click.
export const HUD = {from: 42, to: 93, onlineAt: 85, approveAt: 82};

// Beat 8's tool-call feed: seconds after the beat starts. Placeholder from the 12:05 AM bench run
// (firmware/harness/RESULTS.md); regenerate from the new take's transcript.jsonl.
export const TOOL_FEED: {at: number; name: string; risk: string; result: string}[] = [
  {at: 0.5, name: 'status', risk: 'read', result: 'arm job failed: exit 4'},
  {at: 2.5, name: 'fw_images', risk: 'read', result: 'running v1.7 · known-good v1.6'},
  {at: 5, name: 'fw_rollback', risk: 'flash', result: '✓ verified on chip'},
  {at: 8.5, name: 'arm_run', risk: 'arm', result: '✓ exit 0 · 663/663 frames'},
  {at: 12, name: 'src_diff', risk: 'read', result: 'v1.6 → v1.7: hcsr04.c'},
  {at: 14, name: 'src_read', risk: 'read', result: 'hcsr04.c:122'},
  {at: 16.5, name: 'note', risk: 'read', result: 'root cause: (uint16_t) cast truncates distance'},
];
