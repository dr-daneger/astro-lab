import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
    LineChart, Line, XAxis, YAxis, Tooltip,
    ResponsiveContainer, ReferenceLine,
    ScatterChart, Scatter,
} from 'recharts';

// ────────────────────────────────────────────────────────────────
// TYPES
// ────────────────────────────────────────────────────────────────
interface SimulationMetrics { ripple: number; gradient: number; finalSigma: number; }

interface DesignCandidate {
    id: string; totalLength: number;
    mixingGap: number; slotSpacing: number; defocusGap: number;
    slot1Active: boolean; slot0Active: boolean;
    ripple: number; gradient: number;
}

interface GraphPoint { x: number; intensity: number; }

interface FieldComputation { simData: GraphPoint[]; imageData: ImageData; }

interface CalibrationResult {
    fittedSigma: number; rmsError: number;
    fittedGamma: number; rmsErrorLorentz: number;
    bestModel: 'gaussian' | 'lorentzian';
    measuredPitch: number; pitchPeaks: number[];
    observedProfile: GraphPoint[];
    modelProfile: GraphPoint[];
    altModelProfile: GraphPoint[];
    residual: GraphPoint[];
}

// ────────────────────────────────────────────────────────────────
// CONSTANTS
// ────────────────────────────────────────────────────────────────
const LED_PITCH = 15;
const LED_COLS = 13;
const LED_ROWS = 9;
const DEFAULT_PTFE_SIGMA = 20;      // mm — default, overridden by calibration
const DEW_SHIELD_LENGTH = 80.3;
const APERTURE_DIAMETER = 75;
const FIELD_SIZE = 200;
const GEOMETRY_STEP = 5;

// Calibration image scales (ruler-crop pixel counting)
const LIGHTSOURCE_UM_PX = 14.58;
const PTFE_UM_PX = 17.30;
const LIGHTSOURCE_MM_PX = LIGHTSOURCE_UM_PX / 1000;
const PTFE_MM_PX = PTFE_UM_PX / 1000;

// Smoothing kernel for profile extraction (pixels)
const PROFILE_SMOOTH_PX = 101;
const PROFILE_BAND_ROWS = 50;  // ±50 rows averaged around center

// ────────────────────────────────────────────────────────────────
// SMALL UI COMPONENTS
// ────────────────────────────────────────────────────────────────
const ControlSlider = ({ label, val, set, min, maxVal, warning, step = 1, unit = 'mm' }: {
    label: string; val: number; set: (v: number) => void;
    min: number; maxVal: number; warning?: boolean; step?: number; unit?: string;
}) => (
    <div className="bg-gray-900 p-3 rounded border border-gray-800">
        <div className="flex justify-between mb-1">
            <div className="text-sm font-semibold text-gray-300">{label}</div>
            <div className={`font-mono ${warning ? 'text-red-400' : 'text-cyan-400'}`}>
                {val}{unit}
            </div>
        </div>
        <input type="range" min={min} max={maxVal} step={step} value={val}
            onChange={e => set(Number(e.target.value))}
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-cyan-500" />
        {warning && <div className="text-xs text-red-400 mt-1">⚠️ Too close!</div>}
    </div>
);

const ToggleSwitch = ({ label, active, toggle }: {
    label: string; active: boolean; toggle: () => void;
}) => (
    <button onClick={toggle} className={`flex items-center justify-between w-full p-3 rounded border ${active ? 'bg-cyan-900/30 border-cyan-700' : 'bg-gray-900 border-gray-800'}`}>
        <span className={`text-sm font-semibold ${active ? 'text-cyan-300' : 'text-gray-500'}`}>{label}</span>
        <div className={`w-10 h-5 rounded-full relative transition-colors ${active ? 'bg-cyan-500' : 'bg-gray-700'}`}>
            <div className={`absolute top-1 w-3 h-3 rounded-full bg-white transition-all ${active ? 'left-6' : 'left-1'}`} />
        </div>
    </button>
);

const MetricBadge = ({ label, value, color }: { label: string; value: string; color: string }) => (
    <div className="bg-gray-950 p-3 rounded border border-gray-800">
        <div className="text-[10px] text-gray-500 uppercase">{label}</div>
        <div className={`text-lg font-mono ${color}`}>{value}</div>
    </div>
);

const formatRipple = (v: number) =>
    !Number.isFinite(v) ? '0.0000' : v < 0.001 && v > 0 ? v.toExponential(2) : v.toFixed(4);

// ────────────────────────────────────────────────────────────────
// PHYSICS ENGINE
// ────────────────────────────────────────────────────────────────
const calcPerf = (
    mixGap: number, slotGap: number, defGap: number,
    s1: boolean, s0: boolean, ptfeSigma: number,
): SimulationMetrics => {
    const totalThrow = defGap + DEW_SHIELD_LENGTH;
    let sigma = mixGap * 0.3;
    if (s1) sigma = Math.sqrt(sigma * sigma + ptfeSigma * ptfeSigma);
    sigma += slotGap * 0.3;
    if (s0) sigma = Math.sqrt(sigma * sigma + ptfeSigma * ptfeSigma);
    sigma += totalThrow * 0.15;

    const rc = Math.floor(LED_ROWS / 2), cc = Math.floor(LED_COLS / 2);
    let Ip = 0, It = 0;
    for (let r = 0; r < LED_ROWS; r++) {
        for (let c = 0; c < LED_COLS; c++) {
            const lx = (c - cc) * LED_PITCH, ly = (r - rc) * LED_PITCH;
            Ip += Math.exp(-(lx * lx + ly * ly) / (2 * sigma * sigma));
            It += Math.exp(-((lx - LED_PITCH / 2) ** 2 + ly * ly) / (2 * sigma * sigma));
        }
    }
    const ripple = (Ip + It) > 0 ? ((Ip - It) / (Ip + It)) * 100 : 0;

    let Ic = 0;
    const lensR = APERTURE_DIAMETER / 2;
    for (let r = 0; r < LED_ROWS; r++)
        for (let c = 0; c < LED_COLS; c++) {
            const lx = (c - cc) * LED_PITCH, ly = (r - rc) * LED_PITCH;
            Ic += Math.exp(-(lx * lx + (ly - lensR) ** 2) / (2 * sigma * sigma));
        }
    const gradient = Ip > 0 ? ((Ip - Ic) / Ip) * 100 : 0;
    return { ripple, gradient, finalSigma: sigma };
};

// ────────────────────────────────────────────────────────────────
// FIELD RENDERER (2D flux map + cross-section)
// ────────────────────────────────────────────────────────────────
const calcField = (
    mix: number, slot: number, def: number,
    s1: boolean, s0: boolean, ptfeSigma: number,
): FieldComputation => {
    const { finalSigma } = calcPerf(mix, slot, def, s1, s0, ptfeSigma);
    const S = FIELD_SIZE, ctr = S / 2, R = APERTURE_DIAMETER / 2;
    const field = new Float32Array(S * S);
    let lo = Infinity, hi = -Infinity;

    for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
        let val = 0;
        for (let r = 0; r < LED_ROWS; r++) for (let c = 0; c < LED_COLS; c++) {
            const lx = (c - (LED_COLS - 1) / 2) * LED_PITCH + ctr;
            const ly = (r - (LED_ROWS - 1) / 2) * LED_PITCH + ctr;
            val += Math.exp(-((x - lx) ** 2 + (y - ly) ** 2) / (2 * finalSigma * finalSigma));
        }
        if (Math.hypot(x - ctr, y - ctr) > R) val = 0;
        field[y * S + x] = val;
        if (val > 0) { if (val > hi) hi = val; if (val < lo) lo = val; }
    }

    const img = new ImageData(S, S);
    for (let i = 0; i < field.length; i++) {
        const v = field[i];
        const n = hi !== lo ? (v - lo) / (hi - lo) : 0;
        if (v === 0) { img.data[i * 4] = 10; img.data[i * 4 + 1] = 10; img.data[i * 4 + 2] = 10; }
        else { img.data[i * 4] = 70 * n; img.data[i * 4 + 1] = 200 * n; img.data[i * 4 + 2] = 100 + 150 * n; }
        img.data[i * 4 + 3] = 255;
    }

    const row = Math.floor(S / 2);
    const simData: GraphPoint[] = [];
    for (let x = 0; x < S; x++) {
        const v = field[row * S + x];
        if (v > 0) simData.push({ x: x - ctr, intensity: v });
    }
    return { simData, imageData: img };
};

// ────────────────────────────────────────────────────────────────
// OPTIMIZER HELPERS
// ────────────────────────────────────────────────────────────────
const buildCandidates = (maxL: number, ptfeSigma: number): DesignCandidate[] => {
    const states = [
        { s1: true, s0: true }, { s1: true, s0: false }, { s1: false, s0: true },
    ];
    const out: DesignCandidate[] = [];
    for (let m = 0; m <= 100; m += GEOMETRY_STEP)
        for (let s = 5; s <= 30; s += GEOMETRY_STEP)
            for (let d = 10; d <= 100; d += GEOMETRY_STEP) {
                const L = m + s + d;
                if (L > maxL) continue;
                for (const st of states) {
                    const p = calcPerf(m, s, d, st.s1, st.s0, ptfeSigma);
                    out.push({
                        id: `${m}-${s}-${d}-${st.s1 ? 1 : 0}-${st.s0 ? 1 : 0}`,
                        totalLength: L, mixingGap: m, slotSpacing: s, defocusGap: d,
                        slot1Active: st.s1, slot0Active: st.s0,
                        ripple: Math.max(p.ripple, 1e-7), gradient: Math.max(p.gradient, 0),
                    });
                }
            }
    return out;
};

const paretoOf = (cands: DesignCandidate[]): DesignCandidate[] => {
    const sorted = [...cands].sort((a, b) => a.ripple - b.ripple || a.gradient - b.gradient);
    const front: DesignCandidate[] = [];
    let bestG = Infinity;
    for (const c of sorted) if (c.gradient < bestG) { front.push(c); bestG = c.gradient; }
    return front;
};

const balancedOf = (front: DesignCandidate[]): DesignCandidate | null => {
    if (!front.length) return null;
    const rMin = Math.min(...front.map(p => p.ripple)), rMax = Math.max(...front.map(p => p.ripple));
    const gMin = Math.min(...front.map(p => p.gradient)), gMax = Math.max(...front.map(p => p.gradient));
    let best = front[0], bestS = Infinity;
    for (const p of front) {
        const rn = rMax === rMin ? 0 : (p.ripple - rMin) / (rMax - rMin);
        const gn = gMax === gMin ? 0 : (p.gradient - gMin) / (gMax - gMin);
        const s = Math.hypot(rn, gn);
        if (s < bestS) { bestS = s; best = p; }
    }
    return best;
};

// ────────────────────────────────────────────────────────────────
// CALIBRATION ENGINE
// ────────────────────────────────────────────────────────────────

/** 1-D box-car smooth (in-place-safe). */
const smooth1D = (arr: Float64Array, kernel: number): Float64Array => {
    if (kernel <= 1) return arr;
    const half = Math.floor(kernel / 2);
    const out = new Float64Array(arr.length);
    let runSum = 0, count = 0;
    for (let i = 0; i < arr.length; i++) {
        runSum += arr[i]; count++;
        if (i >= kernel) { runSum -= arr[i - kernel]; count--; }
        out[i] = runSum / count;
    }
    // fix edges with direct averaging
    for (let i = 0; i < half && i < arr.length; i++) {
        let s = 0, c = 0;
        for (let j = Math.max(0, i - half); j <= Math.min(arr.length - 1, i + half); j++) { s += arr[j]; c++; }
        out[i] = s / c;
    }
    return out;
};

const extractProfile = (
    imgData: ImageData, mmPerPx: number,
): GraphPoint[] => {
    const w = imgData.width, h = imgData.height;

    // Find brightest row in the central 50% of the image (avoids edge artifacts)
    const marginLo = Math.floor(h * 0.25);
    const marginHi = Math.floor(h * 0.75);
    let bestRow = marginLo, bestSum = 0;
    for (let y = marginLo; y < marginHi; y++) {
        let sum = 0;
        for (let x = 0; x < w; x++) sum += Math.pow(imgData.data[(y * w + x) * 4 + 1] / 255, 2.2);
        if (sum > bestSum) { bestSum = sum; bestRow = y; }
    }

    // Average ±PROFILE_BAND_ROWS around the brightest row to reduce noise
    const r0 = Math.max(0, bestRow - PROFILE_BAND_ROWS);
    const r1 = Math.min(h, bestRow + PROFILE_BAND_ROWS + 1);
    const nRows = r1 - r0;
    const raw = new Float64Array(w);
    for (let y = r0; y < r1; y++) {
        for (let x = 0; x < w; x++) {
            raw[x] += Math.pow(imgData.data[(y * w + x) * 4 + 1] / 255, 2.2);
        }
    }
    for (let x = 0; x < w; x++) raw[x] /= nRows;

    // Smooth to suppress pixel noise
    const smoothed = smooth1D(raw, PROFILE_SMOOTH_PX);

    const center = w / 2;
    const profile: GraphPoint[] = [];
    for (let x = 0; x < w; x++) {
        profile.push({ x: (x - center) * mmPerPx, intensity: smoothed[x] });
    }
    return profile;
};

/** Find peaks with a minimum distance in mm and prominence threshold. */
const findPeaks = (profile: GraphPoint[], minSepMm: number, prominenceFrac = 0.05): number[] => {
    const vals = profile.map(p => p.intensity);
    const vMax = Math.max(...vals);
    const prominenceThreshold = vMax * prominenceFrac;
    const peaks: number[] = [];

    // Wider window (5 samples each side) to avoid noise peaks
    for (let i = 5; i < vals.length - 5; i++) {
        let isMax = true;
        for (let d = 1; d <= 5; d++) {
            if (vals[i] < vals[i - d] || vals[i] < vals[i + d]) { isMax = false; break; }
        }
        if (!isMax) continue;

        // Check prominence: drop at least prominenceThreshold below peak
        // on both sides within a reasonable window
        let leftMin = vals[i], rightMin = vals[i];
        const window = Math.min(200, Math.floor(vals.length / 4));
        for (let d = 1; d <= window && i - d >= 0; d++) leftMin = Math.min(leftMin, vals[i - d]);
        for (let d = 1; d <= window && i + d < vals.length; d++) rightMin = Math.min(rightMin, vals[i + d]);
        const prominence = vals[i] - Math.max(leftMin, rightMin);
        if (prominence < prominenceThreshold) continue;

        // Enforce minimum separation
        if (peaks.length > 0 && (profile[i].x - profile[peaks[peaks.length - 1]].x) < minSepMm) {
            // Keep the taller peak
            if (vals[i] > vals[peaks[peaks.length - 1]]) peaks[peaks.length - 1] = i;
            continue;
        }
        peaks.push(i);
    }
    return peaks;
};

const measurePitch = (profile: GraphPoint[], _mmPerPx: number): { pitch: number; peakPositions: number[] } => {
    // Minimum separation = 70% of expected LED pitch (in mm)
    const minSepMm = LED_PITCH * 0.7;
    const peakIdxs = findPeaks(profile, minSepMm);
    const positions = peakIdxs.map(i => profile[i].x);
    if (positions.length < 2) return { pitch: 0, peakPositions: positions };
    const diffs: number[] = [];
    for (let i = 1; i < positions.length; i++) diffs.push(positions[i] - positions[i - 1]);
    const pitch = diffs.reduce((a, b) => a + b, 0) / diffs.length;
    return { pitch, peakPositions: positions };
};

const modelGaussian1D = (xPositions: number[], sigma: number): number[] => {
    const rc = (LED_ROWS - 1) / 2, cc = (LED_COLS - 1) / 2;
    return xPositions.map(xmm => {
        let val = 0;
        for (let r = 0; r < LED_ROWS; r++) for (let c = 0; c < LED_COLS; c++) {
            const lx = (c - cc) * LED_PITCH;
            const ly = (r - rc) * LED_PITCH;
            val += Math.exp(-((xmm - lx) ** 2 + ly * ly) / (2 * sigma * sigma));
        }
        return val;
    });
};

const modelLorentzian1D = (xPositions: number[], gamma: number): number[] => {
    const rc = (LED_ROWS - 1) / 2, cc = (LED_COLS - 1) / 2;
    return xPositions.map(xmm => {
        let val = 0;
        for (let r = 0; r < LED_ROWS; r++) for (let c = 0; c < LED_COLS; c++) {
            const lx = (c - cc) * LED_PITCH;
            const ly = (r - rc) * LED_PITCH;
            const d2 = (xmm - lx) ** 2 + ly * ly;
            val += 1.0 / (1.0 + d2 / (gamma * gamma));
        }
        return val;
    });
};

/** Fit both Gaussian and Lorentzian PSF models, return the better one as primary. */
const fitPtfeSigma = (observedProfile: GraphPoint[]): CalibrationResult => {
    // Downsample to every 10th point for fitting speed
    const step = 10;
    const dsProfile = observedProfile.filter((_, i) => i % step === 0);
    const xs = dsProfile.map(p => p.x);
    const obsRaw = dsProfile.map(p => p.intensity);
    const obsMax = Math.max(...obsRaw);
    const obs = obsRaw.map(v => v / obsMax);

    // --- Gaussian sweep ---
    let bestSigma = DEFAULT_PTFE_SIGMA, bestRMSG = Infinity;
    for (let sigma = 2; sigma <= 80; sigma += 0.25) {
        const model = modelGaussian1D(xs, sigma);
        const mMax = Math.max(...model);
        const mNorm = model.map(v => v / mMax);
        let sumSq = 0;
        for (let i = 0; i < obs.length; i++) sumSq += (obs[i] - mNorm[i]) ** 2;
        const rms = Math.sqrt(sumSq / obs.length);
        if (rms < bestRMSG) { bestRMSG = rms; bestSigma = sigma; }
    }

    // --- Lorentzian sweep ---
    let bestGamma = 20, bestRMSL = Infinity;
    for (let gamma = 2; gamma <= 80; gamma += 0.25) {
        const model = modelLorentzian1D(xs, gamma);
        const mMax = Math.max(...model);
        const mNorm = model.map(v => v / mMax);
        let sumSq = 0;
        for (let i = 0; i < obs.length; i++) sumSq += (obs[i] - mNorm[i]) ** 2;
        const rms = Math.sqrt(sumSq / obs.length);
        if (rms < bestRMSL) { bestRMSL = rms; bestGamma = gamma; }
    }

    const bestModel = bestRMSL < bestRMSG ? 'lorentzian' as const : 'gaussian' as const;

    // Build full-resolution profiles for display using ALL points
    const allXs = observedProfile.map(p => p.x);
    const allObs = observedProfile.map(p => p.intensity);
    const allObsMax = Math.max(...allObs);
    const allObsNorm = allObs.map(v => v / allObsMax);

    const gFull = modelGaussian1D(allXs, bestSigma);
    const gMax = Math.max(...gFull);
    const lFull = modelLorentzian1D(allXs, bestGamma);
    const lMax = Math.max(...lFull);

    // Downsample display profiles for chart performance (every 20th pixel)
    const dStep = 20;
    const primary = bestModel === 'lorentzian' ? lFull : gFull;
    const pMax = bestModel === 'lorentzian' ? lMax : gMax;
    const alt = bestModel === 'lorentzian' ? gFull : lFull;
    const aMax = bestModel === 'lorentzian' ? gMax : lMax;

    const modelProfile: GraphPoint[] = [];
    const altModelProfile: GraphPoint[] = [];
    const obsNorm: GraphPoint[] = [];
    const residual: GraphPoint[] = [];
    for (let i = 0; i < allXs.length; i += dStep) {
        const x = allXs[i];
        const o = allObsNorm[i];
        const m = primary[i] / pMax;
        modelProfile.push({ x, intensity: m });
        altModelProfile.push({ x, intensity: alt[i] / aMax });
        obsNorm.push({ x, intensity: o });
        residual.push({ x, intensity: o - m });
    }

    return {
        fittedSigma: bestSigma, rmsError: bestRMSG,
        fittedGamma: bestGamma, rmsErrorLorentz: bestRMSL,
        bestModel,
        measuredPitch: 0, pitchPeaks: [],
        observedProfile: obsNorm, modelProfile, altModelProfile, residual,
    };
};

// ────────────────────────────────────────────────────────────────
// IMAGE LOADER
// ────────────────────────────────────────────────────────────────
const loadImageData = (url: string): Promise<ImageData> =>
    new Promise((resolve, reject) => {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
            const cvs = document.createElement('canvas');
            cvs.width = img.naturalWidth;
            cvs.height = img.naturalHeight;
            const ctx = cvs.getContext('2d')!;
            ctx.drawImage(img, 0, 0);
            resolve(ctx.getImageData(0, 0, cvs.width, cvs.height));
        };
        img.onerror = reject;
        img.src = url;
    });

// ────────────────────────────────────────────────────────────────
// CANVAS PAINTER
// ────────────────────────────────────────────────────────────────
const paintField = (canvas: HTMLCanvasElement | null, field: FieldComputation | null) => {
    if (!canvas || !field) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.putImageData(field.imageData, 0, 0);
    ctx.beginPath();
    ctx.arc(FIELD_SIZE / 2, FIELD_SIZE / 2, APERTURE_DIAMETER / 2, 0, 2 * Math.PI);
    ctx.strokeStyle = '#666'; ctx.lineWidth = 1; ctx.stroke();
};

// ────────────────────────────────────────────────────────────────
// MAIN COMPONENT
// ────────────────────────────────────────────────────────────────
type TabId = 'explore' | 'optimize' | 'calibrate';

const SimulationCanvas = () => {
    // --- Shared state ---
    const [activeTab, setActiveTab] = useState<TabId>('explore');
    const [ptfeSigma, setPtfeSigma] = useState(DEFAULT_PTFE_SIGMA);
    const [isCalibrated, setIsCalibrated] = useState(false);

    // --- Explore state ---
    const [mixGap, setMixGap] = useState(25);
    const [slotGap, setSlotGap] = useState(8);
    const [defGap, setDefGap] = useState(30);
    const [s1, setS1] = useState(true);
    const [s0, setS0] = useState(true);
    const [simData, setSimData] = useState<GraphPoint[]>([]);
    const [ripple, setRipple] = useState(0);
    const [gradient, setGradient] = useState(0);
    const exploreRef = useRef<HTMLCanvasElement>(null);

    // --- Optimize state ---
    const [maxL, setMaxL] = useState(180);
    const [selId, setSelId] = useState<string | null>(null);
    const optRef = useRef<HTMLCanvasElement>(null);

    // --- Calibrate state ---
    const [calResult, setCalResult] = useState<CalibrationResult | null>(null);
    const [calStatus, setCalStatus] = useState<'idle' | 'loading' | 'done' | 'error'>('idle');
    const lsCanvasRef = useRef<HTMLCanvasElement>(null);
    const ptfeCanvasRef = useRef<HTMLCanvasElement>(null);

    // --- Derived: optimizer ---
    const candidates = useMemo(() => buildCandidates(maxL, ptfeSigma), [maxL, ptfeSigma]);
    const pareto = useMemo(() => paretoOf(candidates), [candidates]);
    const balanced = useMemo(() => balancedOf(pareto), [pareto]);
    const selected = useMemo(
        () => candidates.find(c => c.id === selId) ?? balanced ?? null,
        [candidates, selId, balanced],
    );
    const optField = useMemo(() =>
        selected ? calcField(selected.mixingGap, selected.slotSpacing, selected.defocusGap, selected.slot1Active, selected.slot0Active, ptfeSigma) : null,
        [selected, ptfeSigma],
    );

    // --- Explore effect ---
    useEffect(() => {
        if (activeTab !== 'explore') return;
        const m = calcPerf(mixGap, slotGap, defGap, s1, s0, ptfeSigma);
        setRipple(m.ripple); setGradient(m.gradient);
        const f = calcField(mixGap, slotGap, defGap, s1, s0, ptfeSigma);
        paintField(exploreRef.current, f);
        setSimData(f.simData);
    }, [mixGap, slotGap, defGap, s1, s0, activeTab, ptfeSigma]);

    // --- Optimize canvas effect ---
    useEffect(() => { paintField(optRef.current, optField); }, [optField]);

    // --- Auto-select balanced ---
    useEffect(() => { if (!selected && balanced) setSelId(balanced.id); }, [selected, balanced]);

    // --- Apply design to explorer ---
    const applyToExplorer = useCallback(() => {
        if (!selected) return;
        setMixGap(selected.mixingGap); setSlotGap(selected.slotSpacing);
        setDefGap(selected.defocusGap); setS1(selected.slot1Active); setS0(selected.slot0Active);
        setActiveTab('explore');
    }, [selected]);

    // --- Download Pareto CSV ---
    const downloadCSV = useCallback(() => {
        if (!pareto.length) return;
        const hdr = 'Total_Length_mm,Mixing_Gap_mm,Slot_Spacing_mm,Defocus_Gap_mm,Slot1,Slot0,Ripple_Pct,Gradient_Pct\n';
        const rows = pareto.map(r =>
            `${r.totalLength},${r.mixingGap},${r.slotSpacing},${r.defocusGap},${r.slot1Active ? 1 : 0},${r.slot0Active ? 1 : 0},${r.ripple.toFixed(8)},${r.gradient.toFixed(4)}`
        ).join('\n');
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([hdr + rows], { type: 'text/csv' }));
        a.download = 'pareto_optimal_geometries.csv'; a.click();
    }, [pareto]);

    // --- Run calibration ---
    const runCalibration = useCallback(async () => {
        setCalStatus('loading');
        try {
            const [lsData, ptfeData] = await Promise.all([
                loadImageData('/calibration_images/Lightsource.JPG'),
                loadImageData('/calibration_images/PTFE.JPG'),
            ]);

            // Paint thumbnails
            for (const [ref, data] of [[lsCanvasRef, lsData], [ptfeCanvasRef, ptfeData]] as const) {
                const cvs = ref.current;
                if (cvs) {
                    const ctx = cvs.getContext('2d')!;
                    const tmpCvs = document.createElement('canvas');
                    tmpCvs.width = data.width; tmpCvs.height = data.height;
                    tmpCvs.getContext('2d')!.putImageData(data, 0, 0);
                    cvs.width = 300; cvs.height = Math.round(300 * data.height / data.width);
                    ctx.drawImage(tmpCvs, 0, 0, cvs.width, cvs.height);
                }
            }

            // Extract profiles (center-biased row, band-averaged, smoothed)
            const lsProfile = extractProfile(lsData, LIGHTSOURCE_MM_PX);
            const ptfeProfile = extractProfile(ptfeData, PTFE_MM_PX);

            // Measure LED pitch from lightsource
            const { pitch, peakPositions } = measurePitch(lsProfile, LIGHTSOURCE_MM_PX);

            // Fit PTFE sigma (Gaussian + Lorentzian)
            const result = fitPtfeSigma(ptfeProfile);
            result.measuredPitch = pitch;
            result.pitchPeaks = peakPositions;

            setCalResult(result);
            setCalStatus('done');
        } catch (err) {
            console.error('Calibration failed:', err);
            setCalStatus('error');
        }
    }, []);

    // --- Apply calibrated sigma ---
    const applyCalibration = useCallback(() => {
        if (!calResult) return;
        // Apply the better-fitting model's parameter
        setPtfeSigma(calResult.bestModel === 'lorentzian' ? calResult.fittedGamma : calResult.fittedSigma);
        setIsCalibrated(true);
    }, [calResult]);

    // ────────────────────────────────────────────────────────────
    // RENDER
    // ────────────────────────────────────────────────────────────
    const sigmaLabel = isCalibrated
        ? <span className="text-green-400 text-xs font-mono ml-2">σ = {ptfeSigma.toFixed(1)}mm (calibrated)</span>
        : <span className="text-yellow-500 text-xs font-mono ml-2">σ = {ptfeSigma}mm (default)</span>;

    return (
        <div className="flex flex-col gap-6 p-4 bg-gray-950 text-gray-100 font-sans rounded-xl border border-gray-800 max-w-5xl mx-auto min-h-[600px]">
            {/* HEADER */}
            <div className="flex justify-between items-center border-b border-gray-800 pb-4">
                <div>
                    <h2 className="text-xl font-bold text-gray-100">
                        Flat Field Optical Engine {sigmaLabel}
                    </h2>
                    <div className="flex gap-2 mt-2">
                        {(['explore', 'optimize', 'calibrate'] as TabId[]).map(t => (
                            <button key={t} onClick={() => setActiveTab(t)}
                                className={`text-xs uppercase tracking-wide px-3 py-1 rounded transition-colors ${activeTab === t ? 'bg-cyan-900 text-cyan-200' : 'text-gray-500 hover:text-gray-300'}`}>
                                {t}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            {/* ═══════════════ EXPLORE TAB ═══════════════ */}
            {activeTab === 'explore' && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                    <div className="flex flex-col gap-6">
                        <div className="space-y-4">
                            <div className="flex justify-between items-end border-b border-gray-800 pb-2">
                                <span className="text-xs text-gray-500">GEOMETRY (L = {mixGap + slotGap + defGap}mm)</span>
                            </div>
                            <ControlSlider label="Mixing Gap" val={mixGap} set={setMixGap} min={0} maxVal={100} warning={mixGap < 10} />
                            <ControlSlider label="Slot Spacing" val={slotGap} set={setSlotGap} min={2} maxVal={50} />
                            <ControlSlider label="Defocus Gap" val={defGap} set={setDefGap} min={10} maxVal={100} />
                            <div className="flex justify-between items-end border-b border-gray-800 pb-2 mt-4">
                                <span className="text-xs text-gray-500">DIFFUSER STACK</span>
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                <ToggleSwitch label="Slot 1 (Top)" active={s1} toggle={() => setS1(!s1)} />
                                <ToggleSwitch label="Slot 0 (Bottom)" active={s0} toggle={() => setS0(!s0)} />
                            </div>
                        </div>
                        <div className="bg-blue-900/20 p-4 rounded border border-blue-800/50 text-sm text-blue-200">
                            <strong>Performance Metrics:</strong>
                            <div className="grid grid-cols-2 gap-4 mt-3">
                                <div>
                                    <div className="text-[10px] text-gray-400 uppercase">Grid Ripple</div>
                                    <div className={`font-mono text-lg ${ripple < 0.1 ? 'text-green-400' : 'text-red-400'}`}>{formatRipple(ripple)}%</div>
                                </div>
                                <div>
                                    <div className="text-[10px] text-gray-400 uppercase">Field Gradient</div>
                                    <div className="font-mono text-lg text-yellow-400">{gradient.toFixed(2)}%</div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div className="flex flex-col gap-4">
                        <div className="relative aspect-square bg-black rounded-lg border border-gray-800 overflow-hidden flex items-center justify-center">
                            <canvas ref={exploreRef} width={200} height={200} className="w-full h-full object-contain" />
                            <div className="absolute top-2 right-2 text-[10px] text-gray-500">APERTURE FLUX MAP</div>
                        </div>
                        <div className="h-32 bg-gray-900 rounded-lg border border-gray-800 p-2 relative">
                            <ResponsiveContainer width="100%" height="100%">
                                <LineChart data={simData}>
                                    <YAxis domain={['auto', 'auto']} hide />
                                    <ReferenceLine y={simData[0]?.intensity ?? 0} stroke="#444" strokeDasharray="3 3" />
                                    <Line type="monotone" dataKey="intensity" stroke={ripple > 1 ? '#ef4444' : '#22c55e'} strokeWidth={2} dot={false} />
                                </LineChart>
                            </ResponsiveContainer>
                            <div className="absolute bottom-1 right-2 text-[10px] text-gray-600">INTENSITY CROSS-SECTION</div>
                        </div>
                    </div>
                </div>
            )}

            {/* ═══════════════ OPTIMIZE TAB ═══════════════ */}
            {activeTab === 'optimize' && (
                <div className="flex flex-col gap-4 h-full">
                    {/* Controls row */}
                    <div className="bg-gray-900 p-4 rounded border border-gray-800">
                        <div className="flex justify-between items-center mb-3">
                            <div>
                                <h3 className="text-gray-200 font-bold">Multi-Objective Optimizer</h3>
                                <p className="text-xs text-gray-500">Exhaustive search with Pareto extraction under length constraint.</p>
                            </div>
                            <button onClick={downloadCSV} className="bg-gray-700 hover:bg-gray-600 text-white px-4 py-2 rounded text-sm font-bold transition-colors">Export Pareto CSV</button>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                            <ControlSlider label="Max Total Length" val={maxL} set={setMaxL} min={40} maxVal={250} />
                            <MetricBadge label="Feasible Designs" value={String(candidates.length)} color="text-cyan-300" />
                            <MetricBadge label="Pareto Points" value={String(pareto.length)} color="text-green-300" />
                            <MetricBadge label="Balanced Choice"
                                value={balanced ? `${formatRipple(balanced.ripple)}% / ${balanced.gradient.toFixed(2)}%` : 'N/A'}
                                color="text-yellow-300" />
                        </div>
                    </div>

                    {/* Pareto scatter */}
                    <div className="bg-gray-900 rounded border border-gray-800 p-4">
                        <p className="text-xs text-gray-500 mb-2 text-center">Pareto Front: Ripple vs Gradient</p>
                        <div className="h-72">
                            <ResponsiveContainer width="100%" height="100%">
                                <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
                                    <XAxis type="number" dataKey="ripple" name="Ripple" unit="%" stroke="#666" scale="log" domain={[0.0001, 100]} allowDataOverflow />
                                    <YAxis type="number" dataKey="gradient" name="Gradient" unit="%" stroke="#666" domain={[0, 'auto']} />
                                    <Tooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={{ backgroundColor: '#111', border: '1px solid #333' }} />
                                    <Scatter name="Feasible" data={candidates} fill="#4b5563" fillOpacity={0.15} />
                                    <Scatter name="Pareto" data={pareto} fill="#22d3ee" fillOpacity={0.9} />
                                </ScatterChart>
                            </ResponsiveContainer>
                        </div>
                    </div>

                    {/* Selection + preview */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="bg-gray-900 rounded border border-gray-800 p-4">
                            <div className="flex items-center justify-between mb-3">
                                <h4 className="text-sm font-semibold text-gray-200">Selected Candidate</h4>
                                <button onClick={applyToExplorer} disabled={!selected}
                                    className="bg-cyan-700 hover:bg-cyan-600 disabled:bg-gray-700 text-white px-3 py-1 rounded text-xs font-bold transition-colors">
                                    Apply to Explorer
                                </button>
                            </div>
                            {selected ? (
                                <div className="text-xs font-mono text-gray-300 space-y-2">
                                    <div className="grid grid-cols-2 gap-2">
                                        <MetricBadge label="Total L" value={`${selected.totalLength}mm`} color="text-cyan-300" />
                                        <MetricBadge label="Ripple" value={`${formatRipple(selected.ripple)}%`} color="text-green-300" />
                                        <MetricBadge label="Mix Gap" value={`${selected.mixingGap}mm`} color="text-cyan-300" />
                                        <MetricBadge label="Gradient" value={`${selected.gradient.toFixed(2)}%`} color="text-yellow-300" />
                                        <MetricBadge label="Slot Gap" value={`${selected.slotSpacing}mm`} color="text-cyan-300" />
                                        <MetricBadge label="Defocus" value={`${selected.defocusGap}mm`} color="text-cyan-300" />
                                    </div>
                                    <div className="text-gray-500">Stack: {selected.slot1Active ? 'S1 ON' : 'S1 OFF'} / {selected.slot0Active ? 'S0 ON' : 'S0 OFF'}</div>
                                </div>
                            ) : <div className="text-gray-500 text-sm">No feasible design.</div>}
                            <div className="mt-4 max-h-48 overflow-auto border border-gray-800 rounded">
                                <table className="w-full text-xs font-mono text-left">
                                    <thead className="text-gray-500 border-b border-gray-700 bg-gray-950 sticky top-0">
                                        <tr>
                                            <th className="py-2 px-2">L</th><th className="py-2 px-2">Mix</th>
                                            <th className="py-2 px-2">Slot</th><th className="py-2 px-2">Def</th>
                                            <th className="py-2 px-2 text-right">Ripple</th>
                                            <th className="py-2 px-2 text-right">Grad</th>
                                        </tr>
                                    </thead>
                                    <tbody className="text-gray-300">
                                        {pareto.slice(0, 100).map(c => (
                                            <tr key={c.id} onClick={() => setSelId(c.id)}
                                                className={`border-b border-gray-800 cursor-pointer hover:bg-gray-800/50 ${selected?.id === c.id ? 'bg-cyan-900/20' : ''}`}>
                                                <td className="py-1 px-2">{c.totalLength}</td>
                                                <td className="py-1 px-2">{c.mixingGap}</td>
                                                <td className="py-1 px-2">{c.slotSpacing}</td>
                                                <td className="py-1 px-2">{c.defocusGap}</td>
                                                <td className="py-1 px-2 text-right text-green-300">{formatRipple(c.ripple)}</td>
                                                <td className="py-1 px-2 text-right text-yellow-300">{c.gradient.toFixed(2)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                        <div className="bg-gray-900 rounded border border-gray-800 p-4">
                            <h4 className="text-sm font-semibold text-gray-200 mb-3">Selected Flux Preview</h4>
                            <div className="relative aspect-square bg-black rounded-lg border border-gray-800 overflow-hidden flex items-center justify-center">
                                <canvas ref={optRef} width={200} height={200} className="w-full h-full object-contain" />
                                <div className="absolute top-2 right-2 text-[10px] text-gray-500">OPTIMIZED FLUX MAP</div>
                            </div>
                            <div className="h-32 bg-gray-950 rounded-lg border border-gray-800 p-2 mt-4">
                                <ResponsiveContainer width="100%" height="100%">
                                    <LineChart data={optField?.simData ?? []}>
                                        <YAxis domain={['auto', 'auto']} hide />
                                        <Line type="monotone" dataKey="intensity" stroke="#22d3ee" strokeWidth={2} dot={false} />
                                    </LineChart>
                                </ResponsiveContainer>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* ═══════════════ CALIBRATE TAB ═══════════════ */}
            {activeTab === 'calibrate' && (
                <div className="flex flex-col gap-4 h-full">
                    {/* Status + action */}
                    <div className="bg-gray-900 p-4 rounded border border-gray-800 flex justify-between items-center">
                        <div>
                            <h3 className="text-gray-200 font-bold">Empirical Calibration</h3>
                            <p className="text-xs text-gray-500">
                                Fits PTFE diffusion σ from captured images. Validates LED pitch.
                            </p>
                        </div>
                        <div className="flex gap-3">
                            <button onClick={runCalibration} disabled={calStatus === 'loading'}
                                className="bg-cyan-700 hover:bg-cyan-600 disabled:bg-gray-700 text-white px-4 py-2 rounded text-sm font-bold transition-colors">
                                {calStatus === 'loading' ? 'Processing...' : 'Run Calibration'}
                            </button>
                            {calResult && (
                                <button onClick={applyCalibration}
                                    className="bg-green-700 hover:bg-green-600 text-white px-4 py-2 rounded text-sm font-bold transition-colors">
                                    Apply {calResult.bestModel === 'lorentzian' ? `γ = ${calResult.fittedGamma.toFixed(1)}` : `σ = ${calResult.fittedSigma.toFixed(1)}`}mm
                                </button>
                            )}
                        </div>
                    </div>

                    {/* Image thumbnails */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="bg-gray-900 rounded border border-gray-800 p-4">
                            <div className="flex justify-between mb-2">
                                <h4 className="text-sm font-semibold text-gray-200">Lightsource (raw)</h4>
                                <span className="text-xs text-gray-500 font-mono">{LIGHTSOURCE_UM_PX} µm/px</span>
                            </div>
                            <canvas ref={lsCanvasRef} className="w-full rounded border border-gray-800 bg-black" />
                        </div>
                        <div className="bg-gray-900 rounded border border-gray-800 p-4">
                            <div className="flex justify-between mb-2">
                                <h4 className="text-sm font-semibold text-gray-200">PTFE (diffused, contact)</h4>
                                <span className="text-xs text-gray-500 font-mono">{PTFE_UM_PX} µm/px</span>
                            </div>
                            <canvas ref={ptfeCanvasRef} className="w-full rounded border border-gray-800 bg-black" />
                        </div>
                    </div>

                    {calStatus === 'error' && (
                        <div className="bg-red-900/30 p-4 rounded border border-red-800 text-red-300 text-sm">
                            Failed to load calibration images. Ensure Lightsource.JPG and PTFE.JPG are in public/calibration_images/.
                        </div>
                    )}

                    {calResult && (
                        <>
                            {/* Pitch validation */}
                            <div className="bg-gray-900 rounded border border-gray-800 p-4">
                                <div className="flex justify-between items-center mb-2">
                                    <h4 className="text-sm font-semibold text-gray-200">LED Pitch Validation</h4>
                                    <div className="flex gap-4 text-xs font-mono">
                                        <span>Measured: <span className={Math.abs(calResult.measuredPitch - LED_PITCH) < 1 ? 'text-green-400' : 'text-red-400'}>{calResult.measuredPitch.toFixed(2)}mm</span></span>
                                        <span>Expected: <span className="text-gray-400">{LED_PITCH}mm</span></span>
                                        <span>Peaks found: <span className="text-cyan-400">{calResult.pitchPeaks.length}</span></span>
                                    </div>
                                </div>
                                {Math.abs(calResult.measuredPitch - LED_PITCH) < 1.5
                                    ? <p className="text-xs text-green-400">✓ Scale calibration consistent with expected LED pitch.</p>
                                    : calResult.measuredPitch === 0
                                        ? <p className="text-xs text-gray-400">No clear peaks detected — LED structure may be fully diffused or outside field of view.</p>
                                        : <p className="text-xs text-yellow-400">⚠ Measured pitch deviates from expected {LED_PITCH}mm — verify ruler-crop scale calibration.</p>
                                }
                            </div>

                            {/* Sigma fit results */}
                            <div className="bg-gray-900 rounded border border-gray-800 p-4">
                                <div className="flex justify-between items-center mb-2">
                                    <h4 className="text-sm font-semibold text-gray-200">Diffuser PSF Fit</h4>
                                    <div className="flex gap-4 text-xs font-mono">
                                        <span className={calResult.bestModel === 'gaussian' ? 'text-green-400 font-bold' : 'text-gray-500'}>Gauss σ={calResult.fittedSigma.toFixed(1)}mm (RMS {calResult.rmsError.toFixed(4)})</span>
                                        <span className={calResult.bestModel === 'lorentzian' ? 'text-green-400 font-bold' : 'text-gray-500'}>Lorentz γ={calResult.fittedGamma.toFixed(1)}mm (RMS {calResult.rmsErrorLorentz.toFixed(4)})</span>
                                    </div>
                                </div>
                                <div className="text-xs text-gray-400 mb-3">
                                    Best: <span className="text-cyan-300 font-semibold">{calResult.bestModel === 'lorentzian' ? `Lorentzian γ=${calResult.fittedGamma.toFixed(1)}mm` : `Gaussian σ=${calResult.fittedSigma.toFixed(1)}mm`}</span>
                                    {calResult.bestModel === 'lorentzian' && <span className="text-gray-500 ml-2">(Lorentzian captures heavy tails from PTFE volume scattering)</span>}
                                </div>

                                {/* Overlay chart: observed vs both models */}
                                <p className="text-xs text-gray-500 mb-1 text-center">Observed (cyan) vs {calResult.bestModel === 'lorentzian' ? 'Lorentzian' : 'Gaussian'} (green) vs {calResult.bestModel === 'lorentzian' ? 'Gaussian' : 'Lorentzian'} (dim)</p>
                                <div className="h-48">
                                    <ResponsiveContainer width="100%" height="100%">
                                        <LineChart>
                                            <XAxis dataKey="x" type="number" stroke="#666" tick={{ fontSize: 10 }}
                                                label={{ value: 'mm', position: 'insideBottomRight', offset: -5, fontSize: 10, fill: '#666' }} />
                                            <YAxis domain={[0, 1.05]} stroke="#666" tick={{ fontSize: 10 }} />
                                            <Tooltip contentStyle={{ backgroundColor: '#111', border: '1px solid #333' }} />
                                            <Line data={calResult.observedProfile} dataKey="intensity" name="Observed"
                                                stroke="#22d3ee" strokeWidth={2} dot={false} />
                                            <Line data={calResult.modelProfile} dataKey="intensity" name={calResult.bestModel === 'lorentzian' ? 'Lorentzian' : 'Gaussian'}
                                                stroke="#22c55e" strokeWidth={2} dot={false} strokeDasharray="6 3" />
                                            <Line data={calResult.altModelProfile} dataKey="intensity" name={calResult.bestModel === 'lorentzian' ? 'Gaussian' : 'Lorentzian'}
                                                stroke="#666" strokeWidth={1} dot={false} strokeDasharray="3 3" />
                                        </LineChart>
                                    </ResponsiveContainer>
                                </div>

                                {/* Residual */}
                                <p className="text-xs text-gray-500 mt-4 mb-1 text-center">Residual (Observed − Model)</p>
                                <div className="h-32">
                                    <ResponsiveContainer width="100%" height="100%">
                                        <LineChart data={calResult.residual}>
                                            <XAxis dataKey="x" type="number" stroke="#666" tick={{ fontSize: 10 }} />
                                            <YAxis domain={['auto', 'auto']} stroke="#666" tick={{ fontSize: 10 }} />
                                            <ReferenceLine y={0} stroke="#444" strokeDasharray="3 3" />
                                            <Line type="monotone" dataKey="intensity" stroke="#f59e0b" strokeWidth={1.5} dot={false} />
                                        </LineChart>
                                    </ResponsiveContainer>
                                </div>

                                {/* Residual interpretation */}
                                <div className="mt-3 text-xs text-gray-400 bg-gray-950 p-3 rounded border border-gray-800">
                                    {(() => {
                                        const bestRMS = Math.min(calResult.rmsError, calResult.rmsErrorLorentz);
                                        if (bestRMS < 0.03) return <span className="text-green-400">✓ Excellent fit — diffusion model is valid for this PTFE sheet. Residual RMS &lt; 3%.</span>;
                                        if (bestRMS < 0.08) return <span className="text-yellow-400">◐ Acceptable fit — model captures the main profile but minor deviations exist.</span>;
                                        if (bestRMS < 0.15) return <span className="text-orange-400">△ Marginal fit — envelope shape is captured but significant noise or structure remains. Profile smoothing helped but RMS &gt; 8%. Results are usable for design guidance.</span>;
                                        return <span className="text-red-400">✗ Poor fit — neither Gaussian nor Lorentzian adequately describe this diffuser. Consider re-capturing with longer exposure or cleaner background.</span>;
                                    })()}
                                </div>
                            </div>
                        </>
                    )}

                    {calStatus === 'idle' && (
                        <div className="flex-1 flex items-center justify-center text-gray-600 italic">
                            Click "Run Calibration" to load images and fit the diffusion model.
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default SimulationCanvas;
