import { useCallback, useRef, useState } from "react";
import Viewer, { type Sim } from "./Viewer";
import { cn } from "./utils/cn";

const STAGES = [
  { label: "Wheels", from: 0, to: 0.28 },
  { label: "Ramp", from: 0.28, to: 0.6 },
  { label: "Handle", from: 0.6, to: 0.92 },
  { label: "Lock", from: 0.92, to: 1 },
];

export default function App() {
  const sim = useRef<Sim>({ p: 1, pT: 1, m: 0, mT: 0, machine: false });
  const [p, setP] = useState(1);
  const [m, setM] = useState(0);
  const [target, setTarget] = useState(1);
  const [machine, setMachine] = useState(false);

  const onTick = useCallback((np: number, nm: number) => {
    setP(np);
    setM(nm);
  }, []);

  const deployed = p > 0.999;
  const folded = p < 0.001;
  const busy = !deployed && !folded && sim.current.p !== sim.current.pT;

  const toggleFold = () => {
    const next = target > 0.5 ? 0 : 1;
    if (next === 0 && sim.current.machine) {
      sim.current.machine = false;
      setMachine(false);
    }
    sim.current.pT = next;
    setTarget(next);
  };

  const status = deployed
    ? m > 0.99
      ? "Deployed · ramp locked as wall"
      : "Deployed · ramp lowered"
    : folded
      ? "Folded flat · ready to carry"
      : sim.current.pT > sim.current.p
        ? "Unfolding…"
        : "Folding…";

  const setRampMode = (wall: boolean) => {
    sim.current.mT = wall ? 1 : 0;
  };
  const toggleMachine = () => {
    const v = !machine;
    sim.current.machine = v;
    setMachine(v);
  };
  const view = (v: Sim["view"]) => {
    sim.current.view = v;
  };

  return (
    <div className="fixed inset-0 overflow-hidden bg-[#dde2ee] font-sans text-slate-800 select-none">
      <Viewer sim={sim} onTick={onTick} />

      {/* title */}
      <header className="pointer-events-none absolute left-5 top-5 max-w-xs">
        <div className="rounded-2xl bg-white/75 px-5 py-4 shadow-lg shadow-slate-900/10 ring-1 ring-white/70 backdrop-blur-md">
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-indigo-500">Interactive 3D model</p>
          <h1 className="mt-1 text-xl font-semibold leading-tight text-slate-900">Folding Platform Truck</h1>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">
            Appliance mover with lipped deck, foldable wheels, foldable handle and a full‑width ramp that locks up as a
            wall.
          </p>
        </div>
      </header>

      {/* spec card */}
      <aside className="absolute right-5 top-5 hidden w-60 rounded-2xl bg-white/75 p-4 text-xs shadow-lg shadow-slate-900/10 ring-1 ring-white/70 backdrop-blur-md md:block">
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-400">Specification</p>
        <dl className="space-y-1.5">
          {[
            ["Platform", "720 × 720 mm square"],
            ["Suits", "600 × 600 × 850 mm washer"],
            ["Perimeter lip", "30 mm"],
            ["Ramp", "720 × 450 mm, full side"],
            ["Wheels", "4 × Ø100 mm, fold flat"],
            ["Ramp lock", "2 × hook stay + wing nut"],
            ["Handle height", "≈ 800 mm"],
            ["Folded thickness", "≈ 130 mm"],
          ].map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3">
              <dt className="text-slate-500">{k}</dt>
              <dd className="text-right font-medium text-slate-800">{v}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 border-t border-slate-200 pt-3 leading-relaxed text-slate-500">
          Drag to orbit · right‑drag to pan · scroll to zoom. Orbit is unrestricted, so you can look underneath.
        </p>
      </aside>

      {/* controls */}
      <div className="absolute inset-x-0 bottom-4 flex justify-center px-3">
        <div className="w-full max-w-3xl rounded-3xl bg-white/80 p-4 shadow-xl shadow-slate-900/15 ring-1 ring-white/70 backdrop-blur-md">
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={toggleFold}
              className={cn(
                "flex h-11 min-w-[9.5rem] items-center justify-center gap-2 rounded-full px-5 text-sm font-semibold text-white shadow-md transition active:scale-95",
                target > 0.5 ? "bg-indigo-600 hover:bg-indigo-500" : "bg-emerald-600 hover:bg-emerald-500"
              )}
            >
              {target > 0.5 ? (
                <>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                  Play fold
                </>
              ) : (
                <>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                  Play unfold
                </>
              )}
            </button>

            <div className="flex items-center gap-2 text-xs">
              <span
                className={cn(
                  "h-2 w-2 rounded-full",
                  busy ? "animate-pulse bg-amber-500" : deployed ? "bg-indigo-500" : "bg-emerald-500"
                )}
              />
              <span className="font-medium text-slate-600">{status}</span>
            </div>

            <div className="ml-auto flex items-center gap-1 rounded-full bg-slate-100 p-1 text-xs font-medium">
              {(
                [
                  ["Ramp down", false],
                  ["Ramp as wall", true],
                ] as const
              ).map(([label, wall]) => {
                const active = wall ? m > 0.5 || sim.current.mT === 1 : !(m > 0.5 || sim.current.mT === 1);
                return (
                  <button
                    key={label}
                    disabled={!deployed}
                    onClick={() => setRampMode(wall)}
                    className={cn(
                      "rounded-full px-3 py-1.5 transition",
                      active ? "bg-white text-indigo-700 shadow" : "text-slate-500 hover:text-slate-800",
                      !deployed && "cursor-not-allowed opacity-40"
                    )}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* scrubber */}
          <div className="mt-4">
            <div className="relative">
              <input
                type="range"
                min={0}
                max={1}
                step={0.001}
                value={p}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  sim.current.p = v;
                  sim.current.pT = v;
                  if (sim.current.machine && v < 0.999) {
                    sim.current.machine = false;
                    setMachine(false);
                  }
                  setTarget(v > 0.5 ? 1 : 0);
                  setP(v);
                }}
                className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-slate-200 accent-indigo-600"
                aria-label="Fold progress"
              />
            </div>
            <div className="mt-1.5 flex text-[10px] font-medium uppercase tracking-wider text-slate-400">
              {STAGES.map((s) => (
                <span
                  key={s.label}
                  style={{ width: `${(s.to - s.from) * 100}%` }}
                  className={cn(
                    "overflow-hidden text-ellipsis whitespace-nowrap border-l border-slate-200 pl-1 transition-colors first:border-l-0",
                    p > s.from && p < s.to && "font-semibold text-indigo-600"
                  )}
                >
                  {s.label}
                </span>
              ))}
            </div>
            <p className="mt-1 text-center text-[10px] text-slate-400">
              Left = folded flat, right = fully open. Folding runs right → left: handle, ramp, then wheels.
            </p>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-200 pt-3 text-xs">
            <span className="mr-1 font-semibold uppercase tracking-wider text-slate-400">View</span>
            {(
              [
                ["Reset", "reset"],
                ["Top", "top"],
                ["Side", "side"],
                ["Front", "front"],
              ] as const
            ).map(([label, v]) => (
              <button
                key={v}
                onClick={() => view(v)}
                className="rounded-full bg-slate-100 px-3 py-1.5 font-medium text-slate-600 transition hover:bg-slate-200"
              >
                {label}
              </button>
            ))}
            <label
              className={cn(
                "ml-auto flex cursor-pointer items-center gap-2 rounded-full px-3 py-1.5 font-medium transition",
                machine ? "bg-indigo-100 text-indigo-700" : "bg-slate-100 text-slate-600 hover:bg-slate-200",
                !deployed && "cursor-not-allowed opacity-40"
              )}
            >
              <input
                type="checkbox"
                className="accent-indigo-600"
                checked={machine}
                disabled={!deployed}
                onChange={toggleMachine}
              />
              Show washing machine (scale)
            </label>
          </div>
        </div>
      </div>
    </div>
  );
}
