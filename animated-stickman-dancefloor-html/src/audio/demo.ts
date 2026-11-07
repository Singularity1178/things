/** Renders a ~62 second synthesized house track (124 BPM) with a build, breakdown and drop. */
export const DEMO_BPM = 124;

export async function renderDemoTrack(): Promise<{ buffer: AudioBuffer; blob: Blob }> {
  const sr = 44100;
  const beat = 60 / DEMO_BPM;
  const bars = 32;
  const dur = bars * 4 * beat + 1.5;
  const ctx = new OfflineAudioContext(1, Math.ceil(sr * dur), sr);

  const master = ctx.createGain();
  master.gain.value = 0.85;
  const comp = ctx.createDynamicsCompressor();
  comp.threshold.value = -14;
  comp.ratio.value = 4;
  master.connect(comp);
  comp.connect(ctx.destination);

  const noiseBuf = ctx.createBuffer(1, sr, sr);
  const nd = noiseBuf.getChannelData(0);
  for (let i = 0; i < nd.length; i++) nd[i] = Math.random() * 2 - 1;

  const env = (g: GainNode, t: number, peak: number, dec: number, atk = 0.002) => {
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(peak, t + atk);
    g.gain.exponentialRampToValueAtTime(0.0001, t + atk + dec);
  };

  const kick = (t: number) => {
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.frequency.setValueAtTime(165, t);
    o.frequency.exponentialRampToValueAtTime(45, t + 0.11);
    env(g, t, 1.0, 0.38);
    o.connect(g).connect(master);
    o.start(t);
    o.stop(t + 0.5);
  };
  const noise = (t: number, type: BiquadFilterType, freq: number, peak: number, dec: number) => {
    const s = ctx.createBufferSource();
    s.buffer = noiseBuf;
    const f = ctx.createBiquadFilter();
    f.type = type;
    f.frequency.value = freq;
    const g = ctx.createGain();
    env(g, t, peak, dec);
    s.connect(f).connect(g).connect(master);
    s.start(t, Math.random() * 0.5);
    s.stop(t + dec + 0.05);
  };
  const clap = (t: number) => {
    for (let i = 0; i < 3; i++) noise(t + i * 0.011, 'bandpass', 1500, 0.35, i === 2 ? 0.16 : 0.03);
  };
  const hat = (t: number, open: boolean, v = 0.16) => noise(t, 'highpass', 7500, v, open ? 0.22 : 0.045);
  const tone = (t: number, f: number, len: number, type: OscillatorType, cutoff: number, peak: number) => {
    const o = ctx.createOscillator();
    o.type = type;
    o.frequency.value = f;
    const lp = ctx.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.setValueAtTime(cutoff * 2.5, t);
    lp.frequency.exponentialRampToValueAtTime(cutoff, t + len);
    const g = ctx.createGain();
    env(g, t, peak, len, 0.004);
    o.connect(lp).connect(g).connect(master);
    o.start(t);
    o.stop(t + len + 0.05);
  };

  const bassPat = [55, 0, 55, 82.4, 55, 0, 65.4, 73.4];
  const chord = [220, 261.6, 329.6, 392];
  const arp = [440, 523.3, 659.3, 784, 659.3, 523.3, 440, 392, 440, 523.3, 587.3, 784, 659.3, 587.3, 523.3, 392];

  for (let bar = 0; bar < bars; bar++) {
    const t0 = bar * 4 * beat;
    const intro = bar < 4;
    const build = bar >= 4 && bar < 8;
    const breakdown = bar >= 16 && bar < 20;
    const full = !intro && !breakdown;
    for (let b = 0; b < 4; b++) {
      const t = t0 + b * beat;
      if (!intro && !breakdown) kick(t);
      if (build && b === 0 && bar < 7) kick(t);
      if (full && (b === 1 || b === 3) && bar >= 8) clap(t);
      if (breakdown && (b === 1 || b === 3)) clap(t);
      hat(t + beat / 2, true, full ? 0.2 : 0.12);
      if (!breakdown) {
        hat(t, false, 0.07);
        if (bar >= 4) {
          hat(t + beat / 4, false, 0.05);
          hat(t + (3 * beat) / 4, false, 0.05);
        }
      }
    }
    // bass
    if (!breakdown) {
      for (let i = 0; i < 8; i++) {
        const f = bassPat[i];
        if (f) tone(t0 + i * (beat / 2), f, beat * 0.42, 'sawtooth', 380, 0.34);
      }
    }
    // chord stabs
    if (bar >= 8 || breakdown) {
      for (const pos of [1.5, 2.5, 3.5]) {
        if (!breakdown && pos === 2.5 && bar % 2) continue;
        const t = t0 + pos * beat;
        chord.forEach((f, i) => tone(t, f * (breakdown ? 1 : 1) * (i === 3 && bar % 4 === 3 ? 0.89 : 1), beat * 0.35, 'square', 1500, 0.07));
      }
    }
    // arp in breakdown and the drop
    if (breakdown || bar >= 20) {
      for (let i = 0; i < 16; i++) {
        tone(t0 + i * (beat / 4), arp[(i + (bar % 2) * 4) % 16], beat * 0.22, 'triangle', 3000, breakdown ? 0.12 : 0.1);
      }
    }
    // riser before the drop
    if (bar === 19) noise(t0, 'highpass', 3000, 0.28, beat * 4);
  }
  const buffer = await ctx.startRendering();
  return { buffer, blob: encodeWav(buffer) };
}

function encodeWav(buf: AudioBuffer): Blob {
  const data = buf.getChannelData(0);
  const out = new DataView(new ArrayBuffer(44 + data.length * 2));
  const w = (o: number, s: string) => {
    for (let i = 0; i < s.length; i++) out.setUint8(o + i, s.charCodeAt(i));
  };
  w(0, 'RIFF');
  out.setUint32(4, 36 + data.length * 2, true);
  w(8, 'WAVE');
  w(12, 'fmt ');
  out.setUint32(16, 16, true);
  out.setUint16(20, 1, true);
  out.setUint16(22, 1, true);
  out.setUint32(24, buf.sampleRate, true);
  out.setUint32(28, buf.sampleRate * 2, true);
  out.setUint16(32, 2, true);
  out.setUint16(34, 16, true);
  w(36, 'data');
  out.setUint32(40, data.length * 2, true);
  for (let i = 0; i < data.length; i++) {
    const s = Math.max(-1, Math.min(1, data[i]));
    out.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([out], { type: 'audio/wav' });
}
