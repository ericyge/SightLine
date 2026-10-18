// Directional beeps. expo-audio has no pan control, so a small set of stereo beep files is
// rendered once (cache directory) and the closest pan position is played.
import { createAudioPlayer, type AudioPlayer } from 'expo-audio';
import { File, Paths } from 'expo-file-system';
import { makeBeepWav } from './wav';

const PANS = [-1, -0.75, -0.5, -0.25, 0, 0.25, 0.5, 0.75, 1];
const PITCHES = { normal: 660, boost: 990 };

type Key = `${number}:${'normal' | 'boost'}`;
const players = new Map<Key, AudioPlayer>();

const nearestPan = (pan: number) =>
  PANS.reduce((best, p) => (Math.abs(p - pan) < Math.abs(best - pan) ? p : best), PANS[0]);

/** Render the beep files and create their players. Safe to call repeatedly. */
export function prepareBeeps() {
  if (players.size) return;
  for (const pan of PANS) {
    for (const [name, hz] of Object.entries(PITCHES) as ['normal' | 'boost', number][]) {
      const file = new File(Paths.cache, `sightline-beep-${pan}-${name}.wav`);
      if (!file.exists) file.create();
      file.write(makeBeepWav(pan, hz));
      players.set(`${pan}:${name}`, createAudioPlayer({ uri: file.uri }, { keepAudioSessionActive: true }));
    }
  }
}

/** Play one beep panned to `pan` (-1 left .. 1 right). */
export function playBeep(pan: number, boost: boolean) {
  const player = players.get(`${nearestPan(pan)}:${boost ? 'boost' : 'normal'}`);
  if (!player) return;
  void player.seekTo(0).then(() => {
    try { player.play(); } catch { /* ignore AVAudioSession transition races */ }
  }).catch(() => {});
}

export function releaseBeeps() {
  players.forEach((p) => p.remove());
  players.clear();
}
