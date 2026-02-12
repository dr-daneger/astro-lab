# Flat-Field Illumination Simulator

An interactive browser-based tool for designing flat-field panels for telescope imaging. Simulates LED array illumination through a PTFE diffuser, with real-time feedback on uniformity metrics.

## What It Does

Adjusts geometric parameters (LED pitch, panel-to-aperture distance, PTFE diffuser thickness, dew shield length) and immediately shows:

- **1-D intensity profile** across the focal plane
- **Ripple** (peak-to-valley non-uniformity)
- **Gradient** (center-to-edge falloff)
- **Parameter sweep** mode to find optimal spacing

The simulation models a 13x9 LED grid at 15 mm pitch with Lambertian emission through a scattering PTFE slab, matching common flat-field panel builds for 60-80 mm class telescopes.

## Tech Stack

- React 19 + TypeScript
- Recharts (graphs)
- Tailwind CSS 4
- Vite 8

## Getting Started

```bash
npm install
npm run dev
```

Open `http://localhost:5173` in your browser.

## Build

```bash
npm run build     # outputs to dist/
npm run preview   # preview the production build
```

## Project Structure

```
flatfield-sim/
├── src/
│   ├── OpticalSimulator.tsx   # Main simulation component
│   ├── App.tsx                # App shell
│   └── main.tsx               # Entry point
├── package.json
├── vite.config.ts
└── tsconfig.json
```
