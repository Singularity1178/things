import { useCallback, useEffect, useRef, useState } from 'react';
import { AudioEngine } from './audio/engine';
import { Director } from './dance/choreo';
import { MOVES } from './dance/moves';
import { CameraDirector } from './stage/camera';
import StageCanvas, { DanceInfo } from './components/StageCanvas';

const fmt = (s: number) => {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
};

const INFO0: DanceInfo = {
  moveName: '', nextName: '', bpm: 118, intensity: 0.5, t: 0, duration: 0, playing: false, mode: 'freestyle', hasTrack: false, camName: '',
};

export default function App() {
  const [engine] = useState(() => new AudioEngine());
  const [director] = useState(() => new Director());
  const [camDirector] = useState(() => new CameraDirector());
  const [camOn, setCamOn] = useState(true);
  const [info, setInfo] = useState<DanceInfo>(INFO0);
  const [loading, setLoading] = useState<{ p: number; label: string } | null>(null);
  const [error, setError] = useState('');
  const [trackName, setTrackName] = useState('');
  const [locked, setLocked] = useState<number | null>(null);
  const [showMoves, setShowMoves] = useState(false);
  const [drag, setDrag] = useState(false);
  const [vol, setVol] = useState(0.9);
  const [hideUi, setHideUi] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const onInfo = useCallback((i: DanceInfo) => setInfo(i), []);

  const busy = loading !== null;

  const run = useCallback(
    async (job: () => Promise<void>) => {
      setError('');
      setLoading({ p: 0, label: 'Starting…' });
      try {
        await job();
        director.setAnalysis(engine.analysis);
        camDirector.setAnalysis(engine.analysis);
        setTrackName(engine.trackName);
      } catch (e) {
        console.error(e);
        setError("Couldn't read that file. Try an MP3, WAV, OGG, M4A or FLAC.");
      } finally {
        setLoading(null);
      }
    },
    [director, camDirector, engine],
  );

  const loadFile = useCallback(
    (f: File | undefined | null) => {
      if (!f) return;
      if (!f.type.startsWith('audio') && !/\.(mp3|wav|ogg|m4a|aac|flac|opus|weba|webm)$/i.test(f.name)) {
        setError('That does not look like an audio file.');
        return;
      }
      void run(() => engine.loadFile(f, (p, label) => setLoading({ p, label })));
    },
    [engine, run],
  );

  const loadDemo = useCallback(() => {
    void run(() => engine.loadDemo((p, label) => setLoading({ p, label })));
  }, [engine, run]);

  useEffect(() => {
    director.locked = locked;
  }, [locked, director]);

  useEffect(() => {
    engine.setVolume(vol);
  }, [vol, engine]);

  useEffect(() => {
    const over = (e: DragEvent) => {
      e.preventDefault();
      setDrag(true);
    };
    const leave = (e: DragEvent) => {
      if (e.relatedTarget === null) setDrag(false);
    };
    const drop = (e: DragEvent) => {
      e.preventDefault();
      setDrag(false);
      loadFile(e.dataTransfer?.files?.[0]);
    };
    const key = (e: KeyboardEvent) => {
      if (e.code === 'KeyH' && !e.metaKey && !e.ctrlKey && (e.target as HTMLElement).tagName !== 'INPUT') {
        setHideUi((h) => !h);
        return;
      }
      if (e.code === 'KeyC' && !e.metaKey && !e.ctrlKey && (e.target as HTMLElement).tagName !== 'INPUT') {
        setCamOn((c) => !c);
        return;
      }
      if (e.code === 'Space' && engine.hasTrack && (e.target as HTMLElement).tagName !== 'INPUT') {
        e.preventDefault();
        engine.toggle();
      }
    };
    window.addEventListener('dragover', over);
    window.addEventListener('dragleave', leave);
    window.addEventListener('drop', drop);
    window.addEventListener('keydown', key);
    return () => {
      window.removeEventListener('dragover', over);
      window.removeEventListener('dragleave', leave);
      window.removeEventListener('drop', drop);
      window.removeEventListener('keydown', key);
    };
  }, [engine, loadFile]);

  const status =
    info.mode === 'music' ? 'Dancing to your track' : info.mode === 'idle' ? 'Paused — he is waiting' : 'Freestyle — add some music!';

  return (
    <div className="fixed inset-0 overflow-hidden bg-fuchsia-300 text-white select-none">
      <StageCanvas engine={engine} director={director} camDirector={camDirector} camOn={camOn} onInfo={onInfo} />

      {/* viewfinder */}
      {camOn && !hideUi && (
        <div className="pointer-events-none absolute inset-2 sm:inset-4">
          <span className="absolute left-0 top-0 h-6 w-6 border-l-[3px] border-t-[3px] border-white/80" />
          <span className="absolute right-0 top-0 h-6 w-6 border-r-[3px] border-t-[3px] border-white/80" />
          <span className="absolute bottom-0 left-0 h-6 w-6 border-b-[3px] border-l-[3px] border-white/80" />
          <span className="absolute bottom-0 right-0 h-6 w-6 border-b-[3px] border-r-[3px] border-white/80" />
        </div>
      )}

      {/* header */}
      {!hideUi && (
      <div className="pointer-events-none absolute left-0 right-0 top-0 flex items-start justify-between p-3 sm:p-5">
        <div className="rounded-2xl bg-black/70 px-4 py-2 backdrop-blur-md">
          <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.3em] text-fuchsia-300">
            {camOn && <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-red-500" />}
            {camOn ? 'Rec · live on the dancefloor' : 'Live on the dancefloor'}
          </div>
          <div className="text-lg font-black tracking-tight sm:text-2xl">STICKMAN DISCO</div>
        </div>
        <div className="max-w-[55%] rounded-2xl bg-black/70 px-4 py-2 text-right backdrop-blur-md">
          <div className="text-[10px] font-semibold uppercase tracking-[0.25em] text-cyan-300">
            {locked !== null ? 'Move locked' : 'Now dancing'}
          </div>
          <div key={info.moveName} className="animate-[pop_.35s_ease-out] truncate text-base font-extrabold sm:text-xl">
            {info.moveName || '…'}
          </div>
          {info.mode !== 'idle' && locked === null && info.nextName && (
            <div className="truncate text-[11px] text-white/60">next: {info.nextName}</div>
          )}
          {camOn && info.camName && (
            <div key={info.camName} className="animate-[pop_.25s_ease-out] truncate text-[11px] font-bold uppercase tracking-wider text-yellow-300">
              🎥 {info.camName}
            </div>
          )}
          <div className="mt-1 flex items-center justify-end gap-2 text-[11px] text-white/70">
            <span>{Math.round(info.bpm)} BPM</span>
            <span className="h-1.5 w-20 overflow-hidden rounded-full bg-white/20">
              <span
                className="block h-full rounded-full bg-gradient-to-r from-cyan-300 via-fuchsia-400 to-yellow-300 transition-all duration-200"
                style={{ width: `${Math.round(info.intensity * 100)}%` }}
              />
            </span>
            <span>energy</span>
          </div>
        </div>
      </div>
      )}

      {/* move picker */}
      {showMoves && !hideUi && (
        <div className="absolute bottom-[128px] left-3 right-3 mx-auto max-w-3xl rounded-2xl bg-black/80 p-3 backdrop-blur-md sm:bottom-[116px]">
          <div className="flex flex-wrap justify-center gap-1.5">
            <button
              onClick={() => setLocked(null)}
              className={`rounded-full px-3 py-1 text-xs font-bold transition ${
                locked === null ? 'bg-fuchsia-500 text-white' : 'bg-white/10 text-white/80 hover:bg-white/20'
              }`}
            >
              ✨ Auto choreography
            </button>
            {MOVES.map((m, i) => (
              <button
                key={m.name}
                onClick={() => setLocked(i)}
                className={`rounded-full px-3 py-1 text-xs font-semibold transition ${
                  locked === i ? 'bg-cyan-400 text-black' : 'bg-white/10 text-white/80 hover:bg-white/20'
                }`}
              >
                {m.name}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* controls */}
      {!hideUi && (
      <div className="absolute bottom-0 left-0 right-0 p-3 sm:p-5">
        <div className="mx-auto max-w-3xl rounded-3xl bg-black/75 p-3 shadow-2xl backdrop-blur-md sm:p-4">
          <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            <input
              ref={fileRef}
              type="file"
              accept="audio/*,.mp3,.wav,.ogg,.m4a,.aac,.flac"
              className="hidden"
              onChange={(e) => {
                loadFile(e.target.files?.[0]);
                e.target.value = '';
              }}
            />
            <button
              disabled={busy}
              onClick={() => fileRef.current?.click()}
              className="flex items-center gap-2 rounded-full bg-gradient-to-r from-fuchsia-500 to-orange-400 px-4 py-2 text-sm font-bold text-white shadow-lg transition hover:scale-[1.03] disabled:opacity-50"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 16V4M6 10l6-6 6 6M4 20h16" />
              </svg>
              Upload audio
            </button>
            <button
              disabled={busy}
              onClick={loadDemo}
              className="rounded-full bg-white/10 px-4 py-2 text-sm font-semibold text-white transition hover:bg-white/20 disabled:opacity-50"
            >
              ▶ Demo track
            </button>
            <button
              onClick={() => setShowMoves((s) => !s)}
              className={`rounded-full px-4 py-2 text-sm font-semibold transition ${
                showMoves ? 'bg-cyan-400 text-black' : 'bg-white/10 text-white hover:bg-white/20'
              }`}
            >
              🕺 Moves
            </button>
            <button
              onClick={() => setCamOn((c) => !c)}
              role="switch"
              aria-checked={camOn}
              title="Toggle camera angles (C)"
              className={`flex items-center gap-2 rounded-full px-3 py-2 text-sm font-semibold transition ${
                camOn ? 'bg-yellow-300 text-black' : 'bg-white/10 text-white hover:bg-white/20'
              }`}
            >
              🎥 Camera angles
              <span className={`relative h-4 w-7 rounded-full transition ${camOn ? 'bg-black/80' : 'bg-white/30'}`}>
                <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition-all ${camOn ? 'left-3.5' : 'left-0.5'}`} />
              </span>
            </button>
            <div className="ml-auto hidden items-center gap-2 text-xs text-white/60 sm:flex">
              <span>🔈</span>
              <input
                type="range" min={0} max={1} step={0.01} value={vol}
                onChange={(e) => setVol(parseFloat(e.target.value))}
                className="h-1 w-20 accent-fuchsia-400"
              />
            </div>
          </div>

          <div className="mt-3 flex items-center gap-3">
            <button
              disabled={!info.hasTrack || busy}
              onClick={() => engine.toggle()}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-white text-black transition hover:scale-105 disabled:opacity-30"
              aria-label={info.playing ? 'Pause' : 'Play'}
            >
              {info.playing ? (
                <svg viewBox="0 0 24 24" className="h-5 w-5" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>
              ) : (
                <svg viewBox="0 0 24 24" className="h-5 w-5" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
              )}
            </button>
            <div className="min-w-0 flex-1">
              <div className="flex justify-between gap-2 text-[11px] text-white/70">
                <span className="truncate font-semibold text-white/90">{trackName || status}</span>
                <span className="shrink-0 tabular-nums">
                  {info.hasTrack ? `${fmt(info.t)} / ${fmt(info.duration)}` : ''}
                </span>
              </div>
              <input
                type="range" min={0} max={info.duration || 1} step={0.05} value={info.hasTrack ? info.t : 0}
                disabled={!info.hasTrack}
                onChange={(e) => engine.seek(parseFloat(e.target.value))}
                className="mt-1 h-1.5 w-full accent-fuchsia-400 disabled:opacity-30"
              />
              {trackName && <div className="mt-0.5 text-[10px] text-white/45">{status} · Space play/pause · C camera · H hide UI</div>}
            </div>
          </div>
          {error && <div className="mt-2 rounded-lg bg-red-500/20 px-3 py-1.5 text-xs text-red-200">{error}</div>}
        </div>
      </div>
      )}

      {/* hide / show the interface */}
      <button
        onClick={() => setHideUi((h) => !h)}
        title={hideUi ? 'Show interface (H)' : 'Hide interface (H)'}
        aria-label={hideUi ? 'Show interface' : 'Hide interface'}
        className={`absolute right-3 z-10 flex items-center gap-1.5 rounded-full text-xs font-semibold transition sm:right-5 ${
          hideUi
            ? 'bottom-3 bg-black/30 px-3 py-1.5 text-white/60 opacity-40 hover:bg-black/70 hover:text-white hover:opacity-100 sm:bottom-5'
            : 'top-[88px] bg-black/70 px-3 py-1.5 text-white/80 backdrop-blur-md hover:bg-black/90 hover:text-white sm:top-[112px]'
        }`}
      >
        {hideUi ? (
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19M1 1l22 22" />
          </svg>
        )}
        {hideUi ? 'Show UI' : 'Hide UI'}
      </button>

      {/* analysis overlay */}
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="w-[min(88vw,380px)] rounded-3xl bg-black/80 p-6 text-center">
            <div className="text-lg font-extrabold">{loading.label}</div>
            <div className="mt-1 text-xs text-white/60">Mapping the beats so he can dance in time</div>
            <div className="mt-4 h-2 overflow-hidden rounded-full bg-white/15">
              <div
                className="h-full rounded-full bg-gradient-to-r from-cyan-300 via-fuchsia-400 to-orange-300 transition-all"
                style={{ width: `${Math.round(loading.p * 100)}%` }}
              />
            </div>
          </div>
        </div>
      )}

      {/* drop zone */}
      {drag && (
        <div className="pointer-events-none absolute inset-3 flex items-center justify-center rounded-3xl border-4 border-dashed border-white bg-fuchsia-600/50 text-2xl font-black backdrop-blur-sm">
          Drop your song here
        </div>
      )}
    </div>
  );
}
