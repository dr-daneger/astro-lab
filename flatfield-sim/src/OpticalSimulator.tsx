import React, { useState, useEffect, useRef } from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, ScatterChart, Scatter, ZAxis } from 'recharts';

// --- TYPES ---
interface SimulationMetrics {
    ripple: number;
    gradient: number;
    finalSigma: number;
}

interface SweepResult {
    L: number;
    d_mixing: number;
    d_slot: number;
    d_defocus: number;
    ripple: number;
    gradient: number;
}

interface GraphPoint {
    x: number;
    intensity: number;
}

interface ControlSliderProps {
    label: string;
    val: number;
    set: (value: number) => void;
    min: number;
    maxVal: number;
    warning?: boolean;
}

interface ToggleSwitchProps {
    label: string;
    active: boolean;
    toggle: () => void;
}

// --- CONSTANTS ---
const LED_PITCH = 15;        
const LED_COLS = 13;         
const LED_ROWS = 9;          
const PTFE_BASE_WIDTH = 20;  
const DEW_SHIELD_LENGTH = 80.3; 

// --- COMPONENTS ---
const ControlSlider: React.FC<ControlSliderProps> = ({ label, val, set, min, maxVal, warning }) => (
    <div className="bg-gray-900 p-3 rounded border border-gray-800">
        <div className="flex justify-between mb-1">
            <div className="text-sm font-semibold text-gray-300">{label}</div>
            <div className={`font-mono ${warning ? 'text-red-400' : 'text-cyan-400'}`}>
                {val}mm
            </div>
        </div>
        <input 
            type="range" min={min} max={maxVal} value={val} 
            onChange={(e) => set(Number(e.target.value))}
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
        />
        {warning && <div className="text-xs text-red-400 mt-1">⚠️ Too close!</div>}
    </div>
);

const ToggleSwitch: React.FC<ToggleSwitchProps> = ({ label, active, toggle }) => (
    <button onClick={toggle} className={`flex items-center justify-between w-full p-3 rounded border ${active ? 'bg-cyan-900/30 border-cyan-700' : 'bg-gray-900 border-gray-800'}`}>
        <span className={`text-sm font-semibold ${active ? 'text-cyan-300' : 'text-gray-500'}`}>{label}</span>
        <div className={`w-10 h-5 rounded-full relative transition-colors ${active ? 'bg-cyan-500' : 'bg-gray-700'}`}>
            <div className={`absolute top-1 w-3 h-3 rounded-full bg-white transition-all ${active ? 'left-6' : 'left-1'}`} />
        </div>
    </button>
);

// --- PHYSICS ENGINE ---
const calculateOpticalPerformance = (mixingGap: number, slotSpacing: number, defocusGap: number, slot1Active: boolean, slot0Active: boolean): SimulationMetrics => {
    const totalThrow = defocusGap + DEW_SHIELD_LENGTH;
    let sigma = mixingGap * 0.3; 
    if (slot1Active) sigma = Math.sqrt(sigma * sigma + PTFE_BASE_WIDTH * PTFE_BASE_WIDTH);
    sigma += (slotSpacing * 0.3);
    if (slot0Active) sigma = Math.sqrt(sigma * sigma + PTFE_BASE_WIDTH * PTFE_BASE_WIDTH);
    sigma += (totalThrow * 0.15); 

    let I_peak = 0, I_trough = 0;
    const r_center = Math.floor(LED_ROWS/2);
    const c_center = Math.floor(LED_COLS/2);
    
    for (let r = 0; r < LED_ROWS; r++) {
        for (let c = 0; c < LED_COLS; c++) {
            const ledX = (c - c_center) * LED_PITCH;
            const ledY = (r - r_center) * LED_PITCH;
            const distSq_peak = ledX*ledX + ledY*ledY;
            I_peak += Math.exp(-distSq_peak / (2 * sigma * sigma));
            const distSq_trough = (ledX - (LED_PITCH/2))**2 + ledY*ledY;
            I_trough += Math.exp(-distSq_trough / (2 * sigma * sigma));
        }
    }

    const rippleDenominator = I_peak + I_trough;
    const ripple = rippleDenominator > 0 ? ((I_peak - I_trough) / rippleDenominator) * 100 : 0;

    let I_corner = 0;
    const lensR = 37.5; 
    for (let r = 0; r < LED_ROWS; r++) {
        for (let c = 0; c < LED_COLS; c++) {
            const ledX = (c - c_center) * LED_PITCH;
            const ledY = (r - r_center) * LED_PITCH;
            const distSq_corner = ledX*ledX + (ledY - lensR)**2; 
            I_corner += Math.exp(-distSq_corner / (2 * sigma * sigma));
        }
    }
    const gradient = I_peak > 0 ? ((I_peak - I_corner) / I_peak) * 100 : 0;

    return { ripple, gradient, finalSigma: sigma };
};

// --- MAIN COMPONENT ---
const SimulationCanvas = () => {
  const [activeTab, setActiveTab] = useState<'interactive' | 'sweep'>('interactive');
  const [mixingGap, setMixingGap] = useState(25);
  const [slotSpacing, setSlotSpacing] = useState(8);
  const [defocusGap, setDefocusGap] = useState(30);
  const [slot1Active, setSlot1Active] = useState(true);
  const [slot0Active, setSlot0Active] = useState(true);
  const [simData, setSimData] = useState<GraphPoint[]>([]);
  const [gradientMetric, setGradientMetric] = useState(0);
  const [rippleMetric, setRippleMetric] = useState(0);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [sweepResults, setSweepResults] = useState<SweepResult[] | null>(null);
  const [isComputing, setIsComputing] = useState(false);

  useEffect(() => {
    if (activeTab !== 'interactive') return;
    const metrics = calculateOpticalPerformance(mixingGap, slotSpacing, defocusGap, slot1Active, slot0Active);
    setRippleMetric(metrics.ripple);
    setGradientMetric(metrics.gradient);

    const size = 200; 
    const center = size / 2;
    const scale = 1.0; 
    const finalSigma = metrics.finalSigma;
    const finalField = new Float32Array(size * size);
    let minVal = Infinity, maxVal = -Infinity;
    const lensRadiusPx = (75 / 2) * scale;

    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        let intensity = 0;
        for (let r = 0; r < LED_ROWS; r++) {
          for (let c = 0; c < LED_COLS; c++) {
            const ledX = (c - (LED_COLS-1)/2) * LED_PITCH * scale + center;
            const ledY = (r - (LED_ROWS-1)/2) * LED_PITCH * scale + center;
            const distSq = (x - ledX)**2 + (y - ledY)**2;
            intensity += Math.exp(-distSq / (2 * finalSigma * finalSigma));
          }
        }
        const distFromCenter = Math.sqrt((x-center)**2 + (y-center)**2);
        if (distFromCenter > lensRadiusPx) intensity = 0;
        finalField[y * size + x] = intensity;
        if (distFromCenter <= lensRadiusPx) {
            if (intensity > maxVal) maxVal = intensity;
            if (intensity < minVal) minVal = intensity;
        }
      }
    }

    const canvas = canvasRef.current;
    if (canvas) {
        const ctx = canvas.getContext('2d');
        if (ctx) {
            const imgData = ctx.createImageData(size, size);
            for (let i = 0; i < finalField.length; i++) {
                const val = finalField[i];
                let norm = 0;
                if (Number.isFinite(maxVal) && Number.isFinite(minVal) && maxVal !== minVal) norm = (val - minVal) / (maxVal - minVal);
                if (val === 0) { imgData.data[i*4+0] = 10; imgData.data[i*4+1] = 10; imgData.data[i*4+2] = 10; imgData.data[i*4+3] = 255; } 
                else { imgData.data[i*4+0] = 70 * norm; imgData.data[i*4+1] = 200 * norm; imgData.data[i*4+2] = 100 + (150 * norm); imgData.data[i*4+3] = 255; }
            }
            ctx.putImageData(imgData, 0, 0);
            ctx.beginPath(); ctx.arc(center, center, lensRadiusPx, 0, 2 * Math.PI); ctx.strokeStyle = '#666'; ctx.lineWidth = 1; ctx.stroke();
        }
    }

    const graphData: GraphPoint[] = [];
    const sliceRow = Math.floor(size / 2);
    for (let x = 0; x < size; x++) {
        const val = finalField[sliceRow * size + x];
        if (val > 0) graphData.push({ x: x - center, intensity: val });
    }
    setSimData(graphData);
  }, [mixingGap, slotSpacing, defocusGap, slot1Active, slot0Active, activeTab]);

  const runSweep = () => {
      setIsComputing(true);
      setTimeout(() => {
          const results: SweepResult[] = [];
          for (let L = 40; L <= 300; L += 10) {
              for (let d_defocus = 10; d_defocus <= L - 5; d_defocus += 5) {
                  for (let d_slot = 5; d_slot <= 20; d_slot += 5) {
                      const d_mixing = L - d_defocus - d_slot;
                      if (d_mixing >= 0) {
                          const perf = calculateOpticalPerformance(d_mixing, d_slot, d_defocus, true, true);
                          results.push({ L, d_mixing, d_slot, d_defocus, ripple: perf.ripple, gradient: perf.gradient });
                      }
                  }
              }
          }
          setSweepResults(results);
          setIsComputing(false);
      }, 100);
  };

  const downloadCSV = () => {
      if (!sweepResults) return;
      const headers = "Total_Length_mm,Mixing_Gap_mm,Slot_Spacing_mm,Defocus_Gap_mm,Grid_Ripple_Pct,Source_Gradient_Pct\n";
      const rows = sweepResults.map(r => `${r.L},${r.d_mixing},${r.d_slot},${r.d_defocus},${r.ripple.toFixed(8)},${r.gradient.toFixed(4)}`).join("\n");
      const blob = new Blob([headers + rows], { type: 'text/csv' });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = "optical_stack_sweep.csv"; a.click();
  };

  const formatRipple = (val: number) => (!Number.isFinite(val)) ? "0.0000" : (val < 0.001 && val > 0) ? val.toExponential(2) : val.toFixed(4);

  return (
    <div className="flex flex-col gap-6 p-4 bg-gray-950 text-gray-100 font-sans rounded-xl border border-gray-800 max-w-4xl mx-auto min-h-[600px]">
      <div className="flex justify-between items-center border-b border-gray-800 pb-4">
        <div>
            <h2 className="text-xl font-bold text-gray-100">Optical Validation Engine</h2>
            <div className="flex gap-4 mt-2">
                <button onClick={() => setActiveTab('interactive')} className={`text-xs uppercase tracking-wide px-3 py-1 rounded ${activeTab === 'interactive' ? 'bg-cyan-900 text-cyan-200' : 'text-gray-500 hover:text-gray-300'}`}>Interactive</button>
                <button onClick={() => setActiveTab('sweep')} className={`text-xs uppercase tracking-wide px-3 py-1 rounded ${activeTab === 'sweep' ? 'bg-cyan-900 text-cyan-200' : 'text-gray-500 hover:text-gray-300'}`}>Parametric Sweep</button>
            </div>
        </div>
      </div>
      {activeTab === 'interactive' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8 animate-in fade-in duration-300">
            <div className="flex flex-col gap-6">
                <div className="space-y-4">
                    <div className="flex justify-between items-end border-b border-gray-800 pb-2"><span className="text-xs text-gray-500">GEOMETRY (L = {mixingGap + slotSpacing + defocusGap}mm)</span></div>
                    <ControlSlider label="Mixing Gap" val={mixingGap} set={setMixingGap} min={0} maxVal={100} warning={mixingGap < 10} />
                    <ControlSlider label="Slot Spacing" val={slotSpacing} set={setSlotSpacing} min={2} maxVal={50} />
                    <ControlSlider label="Defocus Gap" val={defocusGap} set={setDefocusGap} min={10} maxVal={100} />
                    <div className="flex justify-between items-end border-b border-gray-800 pb-2 mt-6"><span className="text-xs text-gray-500">DIFFUSER STACK</span></div>
                    <div className="grid grid-cols-2 gap-4">
                        <ToggleSwitch label="Slot 1 (Top)" active={slot1Active} toggle={() => setSlot1Active(!slot1Active)} />
                        <ToggleSwitch label="Slot 0 (Bottom)" active={slot0Active} toggle={() => setSlot0Active(!slot0Active)} />
                    </div>
                </div>
                <div className="bg-blue-900/20 p-4 rounded border border-blue-800/50 text-sm text-blue-200">
                    <strong>Performance Metrics:</strong>
                    <div className="grid grid-cols-2 gap-4 mt-3">
                        <div><div className="text-[10px] text-gray-400 uppercase">Grid Ripple</div><div className={`font-mono text-lg ${rippleMetric < 0.1 ? 'text-green-400' : 'text-red-400'}`}>{formatRipple(rippleMetric)}%</div></div>
                        <div><div className="text-[10px] text-gray-400 uppercase">Field Gradient</div><div className="font-mono text-lg text-yellow-400">{gradientMetric.toFixed(2)}%</div></div>
                    </div>
                </div>
            </div>
            <div className="flex flex-col gap-4">
                <div className="relative aspect-square bg-black rounded-lg border border-gray-800 overflow-hidden flex items-center justify-center">
                    <canvas ref={canvasRef} width={200} height={200} className="w-full h-full object-contain" />
                    <div className="absolute top-2 right-2 text-[10px] text-gray-500">APERTURE FLUX MAP</div>
                </div>
                <div className="h-32 bg-gray-900 rounded-lg border border-gray-800 p-2 relative">
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={simData}><YAxis domain={['auto', 'auto']} hide /><ReferenceLine y={simData.length > 0 ? simData[0].intensity : 0} stroke="#444" strokeDasharray="3 3" /><Line type="monotone" dataKey="intensity" stroke={rippleMetric > 1 ? "#ef4444" : "#22c55e"} strokeWidth={2} dot={false} /></LineChart>
                    </ResponsiveContainer>
                    <div className="absolute bottom-1 right-2 text-[10px] text-gray-600">INTENSITY CROSS-SECTION</div>
                </div>
            </div>
        </div>
      )}
      {activeTab === 'sweep' && (
          <div className="flex flex-col gap-4 animate-in fade-in duration-300 h-full">
              <div className="flex justify-between items-center bg-gray-900 p-4 rounded border border-gray-800">
                  <div><h3 className="text-gray-200 font-bold">Parametric Sweep</h3><p className="text-xs text-gray-500">Iterates L=40mm..300mm | Permutes Diffuser Positions</p></div>
                  <div className="flex gap-3">
                      <button onClick={runSweep} disabled={isComputing} className="bg-cyan-700 hover:bg-cyan-600 disabled:bg-gray-700 text-white px-4 py-2 rounded text-sm font-bold transition-colors">{isComputing ? "Computing..." : "Run Simulation"}</button>
                      {sweepResults && (<button onClick={downloadCSV} className="bg-gray-700 hover:bg-gray-600 text-white px-4 py-2 rounded text-sm font-bold flex items-center gap-2 transition-colors">Download CSV</button>)}
                  </div>
              </div>
              {sweepResults ? (
                  <div className="flex-1 overflow-auto bg-gray-900 rounded border border-gray-800 p-4">
                      <div className="h-64 mb-6"><p className="text-xs text-gray-500 mb-2 text-center">Trade-Off Analysis: Ripple vs Length</p><ResponsiveContainer width="100%" height="100%"><ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}><XAxis type="number" dataKey="L" name="Length" unit="mm" stroke="#666" /><YAxis type="number" dataKey="ripple" name="Ripple" unit="%" stroke="#666" scale="log" domain={[0.0001, 100]} allowDataOverflow /><ZAxis type="number" dataKey="d_mixing" range={[10, 100]} name="Mixing Gap" /><Tooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={{backgroundColor: '#111', border: '1px solid #333'}} /><Scatter name="Configurations" data={sweepResults} fill="#8884d8" shape="circle" fillOpacity={0.6} /></ScatterChart></ResponsiveContainer></div>
                      <table className="w-full text-xs font-mono text-left"><thead className="text-gray-500 border-b border-gray-700"><tr><th className="py-2">Length</th><th className="py-2">Mix Gap</th><th className="py-2">Slot Gap</th><th className="py-2">Defocus</th><th className="py-2 text-right">Ripple %</th><th className="py-2 text-right">Grad %</th></tr></thead><tbody className="text-gray-300">{sweepResults.slice(0, 50).map((r, i) => (<tr key={i} className="border-b border-gray-800 hover:bg-gray-800/50"><td className="py-1">{r.L}</td><td className="py-1 text-cyan-500">{r.d_mixing}</td><td className="py-1">{r.d_slot}</td><td className="py-1">{r.d_defocus}</td><td className={`py-1 text-right ${r.ripple < 1 ? 'text-green-400 font-bold' : 'text-red-400'}`}>{formatRipple(r.ripple)}</td><td className="py-1 text-right text-yellow-500">{r.gradient.toFixed(2)}</td></tr>))}</tbody></table>
                  </div>
              ) : (<div className="flex-1 flex items-center justify-center text-gray-600 italic">Awaiting computation.</div>)}
          </div>
      )}
    </div>
  );
};
export default SimulationCanvas;
