import { useEffect, useRef } from 'react';
import type { AudioEngine } from '../audio/engine';
import { Director } from '../dance/choreo';
import { drawRig, solveRig } from '../dance/rig';
import { burst, drawBackdrop, drawFloor, drawFront, makeLayout, makeStageState } from '../stage/stage';

export interface DanceInfo {
  moveName: string;
  nextName: string;
  bpm: number;
  intensity: number;
  t: number;
  duration: number;
  playing: boolean;
  mode: 'music' | 'freestyle' | 'idle';
  hasTrack: boolean;
}

interface Props {
  engine: AudioEngine;
  director: Director;
  onInfo: (i: DanceInfo) => void;
}

export default function StageCanvas({ engine, director, onInfo }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext('2d')!;
    const st = makeStageState();
    let W = 0, H = 0, dpr = 1;
    let raf = 0;
    let last = performance.now();
    let lastInfo = 0;

    const resize = () => {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      W = canvas.clientWidth;
      H = canvas.clientHeight;
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const time = now / 1000;
      const L = makeLayout(W, H);
      const m = engine.frame(now);
      const out = director.update(m, now);
      if (m.dropCrossed) burst(st, L);

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      drawBackdrop(ctx, L, m, st, time);
      drawFloor(ctx, L, m, time);

      const rig = solveRig(out.pose, { cx: L.cx, fy: L.fy, U: L.U });

      // soft contact shadow on the floor
      const lift = Math.min(1, rig.air * 2.2);
      ctx.save();
      ctx.fillStyle = `rgba(0,0,0,${0.3 * (1 - 0.6 * lift)})`;
      ctx.beginPath();
      ctx.ellipse(rig.anchor.x, L.fy + 2, L.U * 0.3 * (1 - 0.3 * lift), L.U * 0.055, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      // glossy floor reflection
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, L.fy, W, H - L.fy);
      ctx.clip();
      ctx.translate(0, 2 * L.fy);
      ctx.scale(1, -1);
      ctx.globalAlpha = 0.16;
      drawRig(ctx, rig, L.U);
      ctx.restore();

      // the dancer: pure black, hollow head
      drawRig(ctx, rig, L.U, '#000000');

      drawFront(ctx, L, st, dt);

      if (now - lastInfo > 120) {
        lastInfo = now;
        onInfo({
          moveName: out.moveName, nextName: out.nextName, bpm: m.bpm, intensity: out.intensity,
          t: engine.el.currentTime, duration: m.duration, playing: m.playing, mode: m.mode, hasTrack: m.hasTrack,
        });
      }
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [engine, director, onInfo]);

  return <canvas ref={ref} className="absolute inset-0 h-full w-full" />;
}
