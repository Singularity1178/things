import { Analysis, analyzeBuffer, at, beatPosAt } from './analysis';
import { renderDemoTrack } from './demo';

export interface MusicState {
  mode: 'music' | 'freestyle' | 'idle';
  playing: boolean;
  t: number;
  dancePos: number; // beats since bar 1 (continuous, bar-aligned)
  bpm: number;
  bass: number; // 0..1
  level: number;
  energy: number;
  onset: number;
  dropCrossed: boolean;
  eq: Float32Array;
  hasTrack: boolean;
  duration: number;
}

const EQ_BARS = 48;

export class AudioEngine {
  el: HTMLAudioElement;
  ctx: AudioContext | null = null;
  analyser: AnalyserNode | null = null;
  analysis: Analysis | null = null;
  trackName = '';
  hasTrack = false;
  private url: string | null = null;
  private freq = new Uint8Array(1024);
  private eq = new Float32Array(EQ_BARS);
  private tSmooth = 0;
  private lastNow = 0;
  private lastT = 0;
  private wasPlaying = false;
  private idleStart = performance.now();

  constructor() {
    this.el = new Audio();
    this.el.preload = 'auto';
    this.el.crossOrigin = 'anonymous';
  }

  private ensureCtx() {
    if (this.ctx) return this.ctx;
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new AC();
    const src = ctx.createMediaElementSource(this.el);
    const an = ctx.createAnalyser();
    an.fftSize = 2048;
    an.smoothingTimeConstant = 0.72;
    src.connect(an);
    an.connect(ctx.destination);
    this.ctx = ctx;
    this.analyser = an;
    this.freq = new Uint8Array(an.frequencyBinCount);
    return ctx;
  }

  /** must be called from a user gesture */
  unlock() {
    const ctx = this.ensureCtx();
    if (ctx.state === 'suspended') void ctx.resume();
  }

  private setSource(blobOrFile: Blob, name: string, loop: boolean) {
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = URL.createObjectURL(blobOrFile);
    this.el.src = this.url;
    this.el.loop = loop;
    this.trackName = name;
  }

  async loadFile(file: File, onProgress: (p: number, label: string) => void) {
    this.unlock();
    const ctx = this.ctx!;
    this.el.pause();
    onProgress(0.02, 'Reading file…');
    const arr = await file.arrayBuffer();
    onProgress(0.08, 'Decoding audio…');
    const buffer = await ctx.decodeAudioData(arr);
    onProgress(0.12, 'Finding the groove…');
    const an = await analyzeBuffer(buffer, (p) => onProgress(0.12 + p * 0.88, 'Finding the groove…'));
    this.finish(file, file.name.replace(/\.[^.]+$/, ''), an, false);
  }

  async loadDemo(onProgress: (p: number, label: string) => void) {
    this.unlock();
    this.el.pause();
    onProgress(0.05, 'Synthesizing a house track…');
    const { buffer, blob } = await renderDemoTrack();
    onProgress(0.3, 'Finding the groove…');
    const an = await analyzeBuffer(buffer, (p) => onProgress(0.3 + p * 0.7, 'Finding the groove…'));
    this.finish(blob, 'Demo Track — House 124', an, true);
  }

  private finish(blob: Blob, name: string, an: Analysis, loop: boolean) {
    this.setSource(blob, name, loop);
    this.analysis = an;
    this.hasTrack = true;
    this.tSmooth = 0;
    this.el.currentTime = 0;
    void this.play();
  }

  async play() {
    this.unlock();
    try {
      await this.el.play();
    } catch {
      /* needs a gesture */
    }
  }
  pause() {
    this.el.pause();
  }
  toggle() {
    if (this.el.paused) void this.play();
    else this.pause();
  }
  seek(t: number) {
    this.el.currentTime = t;
    this.tSmooth = t;
  }
  setVolume(v: number) {
    this.el.volume = v;
  }

  frame(now: number): MusicState {
    const dt = this.lastNow ? Math.min(0.1, (now - this.lastNow) / 1000) : 0.016;
    this.lastNow = now;
    const playing = this.hasTrack && !this.el.paused && !this.el.ended;
    const duration = this.el.duration && isFinite(this.el.duration) ? this.el.duration : this.analysis?.duration ?? 0;

    // spectrum for visuals
    this.readEq(playing);

    const an = this.analysis;
    if (this.hasTrack && an && playing) {
      const ct = this.el.currentTime;
      if (!this.wasPlaying || Math.abs(ct - this.tSmooth) > 0.25) this.tSmooth = ct;
      else {
        this.tSmooth += dt * this.el.playbackRate;
        this.tSmooth += (ct - this.tSmooth) * 0.06;
      }
      this.wasPlaying = true;
      const lat = Math.min(0.3, this.ctx?.outputLatency || this.ctx?.baseLatency || 0);
      const t = Math.max(0, this.tSmooth - lat);
      const pos = beatPosAt(an, t) - an.downbeat;
      let dropCrossed = false;
      if (t > this.lastT) for (const d of an.drops) if (this.lastT < d && t >= d) dropCrossed = true;
      this.lastT = t;
      return {
        mode: 'music', playing: true, t, dancePos: pos, bpm: an.bpm,
        bass: at(an.bass, an.fps, t), level: at(an.level, an.fps, t), energy: at(an.energy, an.fps, t),
        onset: at(an.onset, an.fps, t), dropCrossed, eq: this.eq, hasTrack: true, duration,
      };
    }
    this.wasPlaying = false;
    const s = now / 1000;
    if (this.hasTrack) {
      // paused: calm idle
      const pos = ((now - this.idleStart) / 1000) * (70 / 60);
      return {
        mode: 'idle', playing: false, t: this.el.currentTime, dancePos: pos, bpm: 70, bass: 0, level: 0.05,
        energy: 0.1, onset: 0, dropCrossed: false, eq: this.eq, hasTrack: true, duration,
      };
    }
    // freestyle preview before any music is loaded
    const bpm = 118;
    const pos = s * (bpm / 60);
    const ph = pos - Math.floor(pos);
    const kick = Math.exp(-ph * 5);
    const e = 0.5 + 0.45 * Math.sin(s / 11);
    const seg = Math.floor(pos / 8);
    const dropCrossed = seg !== this.freeSeg && seg % 7 === 6;
    this.freeSeg = seg;
    return {
      mode: 'freestyle', playing: false, t: s, dancePos: pos, bpm, bass: kick * 0.9, level: 0.5 + 0.3 * kick,
      energy: e, onset: kick, dropCrossed, eq: this.eq, hasTrack: false, duration: 0,
    };
  }
  private freeSeg = -1;

  private readEq(playing: boolean) {
    const an = this.analyser;
    let live = false;
    if (an && playing) {
      an.getByteFrequencyData(this.freq);
      let sum = 0;
      for (let i = 0; i < 64; i++) sum += this.freq[i];
      live = sum > 0;
    }
    const bins = this.freq.length;
    for (let i = 0; i < EQ_BARS; i++) {
      let v = 0;
      if (live) {
        const a = Math.floor(Math.pow(i / EQ_BARS, 2) * bins * 0.45) + 1;
        const b = Math.max(a + 1, Math.floor(Math.pow((i + 1) / EQ_BARS, 2) * bins * 0.45) + 1);
        let m = 0;
        for (let k = a; k < b; k++) m = Math.max(m, this.freq[k]);
        v = (m / 255) * (0.75 + (i / EQ_BARS) * 0.55);
      } else {
        const ts = performance.now() / 1000;
        v = 0.12 + 0.1 * Math.sin(ts * 2 + i * 0.5) * Math.sin(ts * 0.7 + i);
      }
      this.eq[i] += (v - this.eq[i]) * (v > this.eq[i] ? 0.6 : 0.18);
    }
  }
}
