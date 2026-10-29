import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { buildCart } from "./cart";

export interface Sim {
  p: number; // 0 = folded flat, 1 = fully deployed
  pT: number;
  m: number; // 0 = ramp lowered, 1 = ramp locked upright as wall
  mT: number;
  machine: boolean;
  view?: "reset" | "top" | "side" | "front";
}

interface Props {
  sim: React.MutableRefObject<Sim>;
  onTick: (p: number, m: number) => void;
}

const VIEWS: Record<string, { pos: [number, number, number]; target: [number, number, number] }> = {
  reset: { pos: [1.55, 1.05, 1.75], target: [0, 0.22, 0.1] },
  top: { pos: [0.001, 2.4, 0.01], target: [0, 0.1, 0.05] },
  side: { pos: [2.5, 0.35, 0.15], target: [0, 0.25, 0.1] },
  front: { pos: [0.1, 0.5, 2.6], target: [0, 0.3, 0] },
};

export default function Viewer({ sim, onTick }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const tickRef = useRef(onTick);
  tickRef.current = onTick;

  useEffect(() => {
    const mount = mountRef.current!;
    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.0;
    mount.appendChild(renderer.domElement);
    renderer.domElement.style.display = "block";
    renderer.domElement.style.touchAction = "none";

    const bg = new THREE.Color(0xdde2ee);
    const scene = new THREE.Scene();
    scene.background = bg;
    scene.fog = new THREE.Fog(bg, 7, 16);

    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environmentIntensity = 0.65;

    const camera = new THREE.PerspectiveCamera(38, 1, 0.01, 60);
    camera.position.set(...VIEWS.reset.pos);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(...VIEWS.reset.target);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = 0.25;
    controls.maxDistance = 8;
    controls.minPolarAngle = 0;
    controls.maxPolarAngle = Math.PI; // fully free orbit (also from below)
    controls.screenSpacePanning = true;
    controls.update();

    // lights
    const sun = new THREE.DirectionalLight(0xfff4e6, 2.6);
    sun.position.set(2.2, 3.6, 2.6);
    sun.castShadow = true;
    sun.shadow.mapSize.set(4096, 4096);
    const sc = sun.shadow.camera;
    sc.left = -1.6;
    sc.right = 1.6;
    sc.top = 1.6;
    sc.bottom = -1.6;
    sc.near = 0.5;
    sc.far = 9;
    sun.shadow.bias = -0.0003;
    sun.shadow.normalBias = 0.0015;
    sun.shadow.radius = 4;
    scene.add(sun);
    const fill = new THREE.DirectionalLight(0xc6d4ff, 0.7);
    fill.position.set(-3, 1.6, -2);
    scene.add(fill);
    scene.add(new THREE.HemisphereLight(0xffffff, 0xb9c0d4, 0.5));

    // floor
    const floor = new THREE.Mesh(
      new THREE.CircleGeometry(9, 96),
      new THREE.MeshStandardMaterial({ color: 0xe9ecf4, roughness: 0.95, metalness: 0 })
    );
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    scene.add(floor);
    const grid = new THREE.GridHelper(12, 60, 0xc3c9db, 0xd0d5e4);
    grid.position.y = 0.0008;
    (grid.material as THREE.Material).transparent = true;
    (grid.material as THREE.Material).opacity = 0.6;
    scene.add(grid);

    const cart = buildCart();
    scene.add(cart.root);

    // resize
    const resize = () => {
      const w = mount.clientWidth;
      const h = mount.clientHeight;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(mount);
    resize();

    // camera fly-to
    let fly: null | { t: number; fromP: THREE.Vector3; toP: THREE.Vector3; fromT: THREE.Vector3; toT: THREE.Vector3 } = null;
    const cancelFly = () => (fly = null);
    renderer.domElement.addEventListener("pointerdown", cancelFly);
    renderer.domElement.addEventListener("wheel", cancelFly, { passive: true });

    const clock = new THREE.Clock();
    let raf = 0;
    let lastP = -1;
    let lastM = -1;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const dt = Math.min(clock.getDelta(), 0.05);
      const s = sim.current;

      if (s.view) {
        const v = VIEWS[s.view];
        fly = {
          t: 0,
          fromP: camera.position.clone(),
          toP: new THREE.Vector3(...v.pos),
          fromT: controls.target.clone(),
          toT: new THREE.Vector3(...v.target),
        };
        s.view = undefined;
      }
      if (fly) {
        fly.t = Math.min(1, fly.t + dt / 0.9);
        const k = fly.t * fly.t * (3 - 2 * fly.t);
        camera.position.lerpVectors(fly.fromP, fly.toP, k);
        controls.target.lerpVectors(fly.fromT, fly.toT, k);
        if (fly.t >= 1) fly = null;
      }

      // advance fold animation
      const stepP = dt / 7;
      if (s.p < s.pT) s.p = Math.min(s.pT, s.p + stepP);
      else if (s.p > s.pT) s.p = Math.max(s.pT, s.p - stepP);
      const stepM = dt / 2.4;
      if (s.m < s.mT) s.m = Math.min(s.mT, s.m + stepM);
      else if (s.m > s.mT) s.m = Math.max(s.mT, s.m - stepM);

      cart.update(s.p, s.m);
      cart.setMachineVisible(s.machine && s.p > 0.999);
      if (Math.abs(s.p - lastP) > 0.0005 || Math.abs(s.m - lastM) > 0.0005) {
        lastP = s.p;
        lastM = s.m;
        tickRef.current(s.p, s.m);
      }

      controls.update();
      renderer.render(scene, camera);
    };
    loop();

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      renderer.dispose();
      pmrem.dispose();
      mount.removeChild(renderer.domElement);
    };
  }, [sim]);

  return <div ref={mountRef} className="absolute inset-0" />;
}
