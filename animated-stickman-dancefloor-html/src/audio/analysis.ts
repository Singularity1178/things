import { FFT } from './fft';

export interface Analysis {
  duration: number;
  fps: number; // analysis frames per second
  bpm: number;
  beats: Float32Array; // beat times (s), covers the whole track
  downbeat: number; // beat index (mod 4) that starts a bar
  onset: Float32Array; // 0..~1 onset strength per frame
  bass: Float32Array; // 0..1
  level: Float32Array; // 0..1 short-term loudness
  energy: Float32Array; // 0..1 long-term section intensity
  drops: number[]; // times of big energy jumps
}

const N = 512;
const HOP = 128;

const tick = () => new Promise<void>((r) => setTimeout(r, 0));

function percentile(arr: ArrayLike<number>, p: number) {
  const c = Float32Array.from(arr as ArrayLike<number>).sort();
  return c[Math.min(c.length - 1, Math.max(0, Math.floor(p * (c.length - 1))))];
}

function boxSmooth(src: Float32Array, radius: number): Float32Array {
  const n = src.length;
  const pre = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) pre[i + 1] = pre[i] + src[i];
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const a = Math.max(0, i - radius), b = Math.min(n, i + radius + 1);
    out[i] = (pre[b] - pre[a]) / (b - a);
  }
  return out;
}

export async function analyzeBuffer(buf: AudioBuffer, onProgress?: (p: number) => void): Promise<Analysis> {
  const sr = buf.sampleRate;
  const c0 = buf.getChannelData(0);
  const c1 = buf.numberOfChannels > 1 ? buf.getChannelData(1) : null;
  const dec = Math.max(1, Math.round(sr / 11025));
  const dsr = sr / dec;
  const n = Math.floor(buf.length / dec);
  const mono = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let k = 0; k < dec; k++) {
      const idx = i * dec + k;
      s += c1 ? (c0[idx] + c1[idx]) * 0.5 : c0[idx];
    }
    mono[i] = s / dec;
  }
  const duration = buf.duration;
  const F = Math.max(8, Math.floor((n - N) / HOP) + 1);
  const fps = dsr / HOP;

  // log-spaced bands
  const NB = 24;
  const edges: number[] = [1];
  for (let i = 1; i <= NB; i++) {
    const e = Math.round(Math.exp(Math.log(1) + (Math.log(250) - Math.log(1)) * (i / NB)));
    edges.push(Math.max(edges[i - 1] + 1, e));
  }
  let bassBands = 0;
  while (bassBands < NB && edges[bassBands + 1] <= 9) bassBands++;
  bassBands = Math.max(2, bassBands);

  const fft = new FFT(N);
  const win = new Float32Array(N);
  for (let i = 0; i < N; i++) win[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (N - 1));
  const re = new Float32Array(N), im = new Float32Array(N);
  const prev = new Float32Array(NB);
  const cur = new Float32Array(NB);
  const onsetRaw = new Float32Array(F);
  const bassL = new Float32Array(F);
  const rms = new Float32Array(F);

  for (let f = 0; f < F; f++) {
    const off = f * HOP;
    let sq = 0;
    for (let i = 0; i < N; i++) {
      const v = mono[off + i] || 0;
      sq += v * v;
      re[i] = v * win[i];
      im[i] = 0;
    }
    rms[f] = Math.sqrt(sq / N);
    fft.transform(re, im);
    let flux = 0, bflux = 0, bl = 0;
    for (let b = 0; b < NB; b++) {
      let m = 0;
      const lo = edges[b], hi = Math.min(edges[b + 1], N / 2 - 1);
      for (let k = lo; k < hi; k++) m += Math.hypot(re[k], im[k]);
      m /= Math.max(1, hi - lo) * (N / 4);
      cur[b] = Math.log1p(m * 400);
      const d = Math.max(0, cur[b] - prev[b]);
      if (f > 0) {
        flux += d;
        if (b < bassBands) bflux += d;
      }
      if (b < bassBands) bl += cur[b];
    }
    onsetRaw[f] = 0.65 * (flux / NB) + 0.35 * (bflux / bassBands);
    bassL[f] = bl / bassBands;
    prev.set(cur);
    if (f % 1200 === 0) {
      onProgress?.(0.8 * (f / F));
      await tick();
    }
  }

  // ----- onset envelope -----
  const localMean = boxSmooth(onsetRaw, Math.round(fps * 0.4));
  const o = new Float32Array(F);
  let mean = 0;
  for (let i = 0; i < F; i++) {
    o[i] = Math.max(0, onsetRaw[i] - localMean[i]);
    mean += o[i];
  }
  mean /= F;
  let varr = 0;
  for (let i = 0; i < F; i++) varr += (o[i] - mean) ** 2;
  const std = Math.sqrt(varr / F) || 1;
  const on = new Float32Array(F);
  for (let i = 0; i < F; i++) on[i] = o[i] / std;
  const p99 = percentile(on, 0.99) || 1;
  const onset = new Float32Array(F);
  for (let i = 0; i < F; i++) onset[i] = Math.min(1.5, on[i] / p99);
  onProgress?.(0.85);
  await tick();

  // ----- tempo (autocorrelation with a prior around 118 BPM) -----
  const minLag = Math.floor((fps * 60) / 190);
  const maxLag = Math.ceil((fps * 60) / 65);
  const R = new Float32Array(maxLag * 2 + 3);
  const centered = new Float32Array(F);
  for (let i = 0; i < F; i++) centered[i] = on[i] - mean / std;
  for (let lag = minLag - 1; lag < R.length; lag++) {
    let s = 0;
    const lim = F - lag;
    if (lim <= 0) break;
    for (let t = 0; t < lim; t++) s += centered[t] * centered[t + lag];
    R[lag] = s / lim;
  }
  let bestLag = Math.round((fps * 60) / 118);
  let bestScore = -Infinity;
  for (let lag = minLag; lag <= maxLag; lag++) {
    const bpm = (60 * fps) / lag;
    const prior = Math.exp(-0.5 * Math.pow(Math.log2(bpm / 118) / 0.85, 2));
    const sc = (R[lag] + 0.5 * R[lag * 2] + 0.25 * R[Math.round(lag * 1.5)]) * prior;
    if (sc > bestScore) {
      bestScore = sc;
      bestLag = lag;
    }
  }
  let P = bestLag;
  {
    const a = R[bestLag - 1], b = R[bestLag], c = R[bestLag + 1];
    const den = a - 2 * b + c;
    if (Math.abs(den) > 1e-9) P = bestLag + (0.5 * (a - c)) / den;
    if (!isFinite(P) || Math.abs(P - bestLag) > 1) P = bestLag;
  }

  // ----- beat tracking (dynamic programming, Ellis 2007) -----
  const tauMin = Math.max(1, Math.round(P / 2));
  const tauMax = Math.round(P * 2);
  const pen = new Float32Array(tauMax + 1);
  for (let tau = tauMin; tau <= tauMax; tau++) pen[tau] = 100 * Math.pow(Math.log(tau / P), 2);
  const C = new Float32Array(F);
  const back = new Int32Array(F).fill(-1);
  for (let t = 0; t < F; t++) {
    let best = -Infinity, arg = -1;
    for (let tau = tauMin; tau <= tauMax; tau++) {
      const p = t - tau;
      if (p < 0) break;
      const v = C[p] - pen[tau];
      if (v > best) {
        best = v;
        arg = p;
      }
    }
    if (arg >= 0 && best > 0) {
      C[t] = on[t] + best;
      back[t] = arg;
    } else {
      C[t] = on[t];
    }
  }
  let end = F - 1;
  {
    let bv = -Infinity;
    for (let t = Math.max(0, F - Math.round(P * 1.5)); t < F; t++) {
      if (C[t] > bv) {
        bv = C[t];
        end = t;
      }
    }
  }
  const frames: number[] = [];
  for (let t = end; t >= 0; t = back[t]) {
    frames.push(t);
    if (back[t] < 0) break;
  }
  frames.reverse();
  const centre = N / 2 / dsr;
  let bt = frames.map((f) => (f * HOP) / dsr + centre);
  const period = 60 / ((60 * fps) / P);
  if (bt.length < 4) bt = [centre];
  // extend to cover whole track
  const firstP = bt.length > 1 ? bt[1] - bt[0] : period;
  const lastP = bt.length > 1 ? bt[bt.length - 1] - bt[bt.length - 2] : period;
  while (bt[0] - firstP >= 0) bt.unshift(bt[0] - firstP);
  while (bt[bt.length - 1] + lastP <= duration) bt.push(bt[bt.length - 1] + lastP);
  const beats = Float32Array.from(bt);
  const avgPeriod = (beats[beats.length - 1] - beats[0]) / Math.max(1, beats.length - 1);
  const bpm = 60 / (avgPeriod || period);

  // ----- downbeat guess: the beat slot (mod 4) with the most bass/onset energy -----
  const bassN = new Float32Array(F);
  const bLo = percentile(bassL, 0.1), bHi = percentile(bassL, 0.98);
  for (let i = 0; i < F; i++) bassN[i] = Math.min(1, Math.max(0, (bassL[i] - bLo) / (bHi - bLo || 1)));
  const slot = [0, 0, 0, 0];
  for (let i = 0; i < beats.length; i++) {
    const f = Math.round(((beats[i] - centre) * dsr) / HOP);
    let m = 0;
    for (let d = -2; d <= 2; d++) {
      const v = onset[f + d] ?? 0;
      if (v > m) m = v;
    }
    slot[i % 4] += m + 0.35 * (bassN[Math.min(F - 1, Math.max(0, f))] ?? 0);
  }
  let downbeat = 0;
  for (let k = 1; k < 4; k++) if (slot[k] > slot[downbeat] * 1.04) downbeat = k;

  // ----- loudness / energy -----
  const lvS = boxSmooth(rms, 2);
  const lp = percentile(lvS, 0.98) || 1e-6;
  const level = new Float32Array(F);
  for (let i = 0; i < F; i++) level[i] = Math.min(1, Math.sqrt(lvS[i] / lp));
  const egS = boxSmooth(rms, Math.round(fps * 0.9));
  const e10 = percentile(egS, 0.08), e95 = percentile(egS, 0.95);
  const energy = new Float32Array(F);
  for (let i = 0; i < F; i++) energy[i] = Math.min(1, Math.max(0, (egS[i] - e10) / (e95 - e10 || 1e-6)));
  const bassS = boxSmooth(bassN, 1);

  // ----- drops -----
  const drops: number[] = [];
  const step = Math.round(fps * 0.25);
  let last = -100;
  const avg = (a: number, b: number) => {
    a = Math.max(0, a);
    b = Math.min(F, b);
    if (b <= a) return 0;
    let s = 0;
    for (let i = a; i < b; i += 2) s += energy[i];
    return s / Math.ceil((b - a) / 2);
  };
  for (let i = Math.round(fps * 3); i < F - fps; i += step) {
    const before = avg(i - Math.round(fps * 3), i);
    const after = avg(i, i + Math.round(fps * 2));
    const t = (i * HOP) / dsr;
    if (after - before > 0.22 && after > 0.45 && t - last > 10) {
      drops.push(t);
      last = t;
    }
  }
  onProgress?.(1);
  return { duration, fps, bpm, beats, downbeat, onset, bass: bassS, level, energy, drops };
}

export function at(arr: Float32Array, fps: number, t: number): number {
  const x = t * fps;
  const i = Math.floor(x);
  if (i < 0) return arr[0] ?? 0;
  if (i >= arr.length - 1) return arr[arr.length - 1] ?? 0;
  const f = x - i;
  return arr[i] * (1 - f) + arr[i + 1] * f;
}

/** continuous beat index at time t (integer = on a beat) */
export function beatPosAt(an: Analysis, t: number): number {
  const B = an.beats;
  const n = B.length;
  if (n < 2) return t * 2;
  if (t <= B[0]) return (t - B[0]) / (B[1] - B[0]);
  if (t >= B[n - 1]) return n - 1 + (t - B[n - 1]) / (B[n - 1] - B[n - 2]);
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (B[mid] <= t) lo = mid;
    else hi = mid;
  }
  return lo + (t - B[lo]) / (B[lo + 1] - B[lo]);
}

export function timeAtBeat(an: Analysis, idx: number): number {
  const B = an.beats;
  const n = B.length;
  if (n < 2) return idx / 2;
  if (idx <= 0) return B[0] + idx * (B[1] - B[0]);
  if (idx >= n - 1) return B[n - 1] + (idx - (n - 1)) * (B[n - 1] - B[n - 2]);
  const i = Math.floor(idx);
  return B[i] + (idx - i) * (B[i + 1] - B[i]);
}
