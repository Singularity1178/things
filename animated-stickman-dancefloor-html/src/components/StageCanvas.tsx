import { useEffect, useRef } from 'react';
import type { AudioEngine } from '../audio/engine';
import { Director } from '../dance/choreo';
import { drawRig, mirrorRigW, projectRig, solveRig, solveRigWorld } from '../dance/rig';
import { CameraDirector, Projector } from '../stage/camera';
import {
  ballDepth, drawBall3D, drawBallSpots, drawBeams3D, drawFloor3D, drawFloorSpot, drawSpeaker3D, drawWalls3D, speakerDepth,
} from '../stage/world';
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
  camName: string;
}

interface Props {
  engine: AudioEngine;
  director: Director;
  camDirector: CameraDirector;
  camOn: boolean;
  onInfo: (i: DanceInfo) => void;
}

export default function StageCanvas({ engine, director, camDirector, camOn, onInfo }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const camOnRef = useRef(camOn);
  camOnRef.current = camOn;

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext('2d')!;
    const st = makeStageState();
    const P = new Projector();
    let wasCam = camOnRef.current;
    let toggleFlash = 0;
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

      const useCam = camOnRef.current;
      if (useCam !== wasCam) {
        wasCam = useCam;
        toggleFlash = 1;
      }
      let camName = '';
      let cutFlash = 0;

      if (!useCam) {
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
      } else {
        // beat bookkeeping for the downbeat flash (normally done by the backdrop)
        const beatIdx = Math.floor(m.dancePos);
        if (beatIdx !== st.lastBeat) {
          st.lastBeat = beatIdx;
          if (beatIdx % 4 === 0) st.flash = 1;
        }

        const rw = solveRigWorld(out.pose);
        const co = camDirector.update(m, now, rw, out.intensity);
        camName = co.name;
        cutFlash = co.cut;
        P.set(co.shot, W, H);

        const lift = Math.min(1, rw.air * 2.2);
        drawFloor3D(ctx, P, W, H, m);
        drawFloorSpot(ctx, P, rw.anchor[0], lift);

        const pelvisPpu = P.project(rw.pelvis).ppu;
        const proj = (v: [number, number, number]) => P.project(v);

        // glossy floor reflection (drawn before the walls so they hide anything below the floor line)
        ctx.save();
        ctx.globalAlpha = 0.16;
        drawRig(ctx, projectRig(mirrorRigW(rw), proj), pelvisPpu);
        ctx.restore();

        drawWalls3D(ctx, P, W, H, m, time);
        drawBallSpots(ctx, P, m, time);
        drawBeams3D(ctx, P, m, time, rw.anchor[0]);

        // objects, far to near
        const items: { z: number; draw: () => void }[] = [
          { z: speakerDepth(P, -1), draw: () => drawSpeaker3D(ctx, P, W, H, -1, m) },
          { z: speakerDepth(P, 1), draw: () => drawSpeaker3D(ctx, P, W, H, 1, m) },
          { z: ballDepth(P), draw: () => drawBall3D(ctx, P, time) },
          { z: P.toCam(rw.pelvis)[2], draw: () => drawRig(ctx, projectRig(rw, proj), pelvisPpu, '#000000') },
        ];
        items.sort((a, b) => b.z - a.z);
        for (const it of items) it.draw();
      }

      drawFront(ctx, L, st, dt);

      // camera-cut flash + toggle flash
      const fl = Math.max(0.3 * cutFlash, 0.5 * toggleFlash);
      if (fl > 0.01) {
        ctx.fillStyle = `rgba(255,255,255,${fl})`;
        ctx.fillRect(0, 0, W, H);
      }
      toggleFlash *= Math.pow(0.002, dt);

      if (now - lastInfo > 120) {
        lastInfo = now;
        onInfo({
          moveName: out.moveName, nextName: out.nextName, bpm: m.bpm, intensity: out.intensity,
          t: engine.el.currentTime, duration: m.duration, playing: m.playing, mode: m.mode, hasTrack: m.hasTrack, camName,
        });
      }
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [engine, director, camDirector, onInfo]);

  return <canvas ref={ref} className="absolute inset-0 h-full w-full" />;
}
