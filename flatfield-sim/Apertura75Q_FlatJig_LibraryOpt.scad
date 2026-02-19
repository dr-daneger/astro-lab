// =====================================================================
// APERTURA 75Q FLAT-FIELD CAPSTONE JIG
// =====================================================================
// Minimal-height jig that sits on the dew shield.
// Stack (bottom->top): Coupler -> PTFE diffuser -> Viltrox L116T
//
// PTFE: Trim sheet to 150 x 128 mm (one straight cut).
// The Viltrox fully covers the PTFE, blocking ambient light leakage.
// =====================================================================

// --- MODE SELECTOR ---
generate_fit_test = false;  // true = 15mm coupler test ring only
show_ghosts        = true;  // true = show non-printing PTFE/Viltrox bodies

// --- 1. SCOPE INTERFACE ---
dew_shield_od   = 102.5;   // mm, measured OD of Apertura 75Q dew shield
bore_clearance  = 0.00;    // mm, diametral clearance for coupler bore (set 0 for exact 102.5 ID)
coupler_h       = 20.0;    // mm, grip depth on dew shield
internal_stop_id = 85.0;   // mm, hard-stop inner bore (prevents over-insertion)

// --- 2. PTFE DIFFUSER (trimmed to match Viltrox short axis) ---
ptfe_w          = 150.0;   // mm, long axis (aligned with Viltrox 192mm side)
ptfe_d          = 128.0;   // mm, short axis (trimmed flush with Viltrox edge dim)
ptfe_h          = 3.0;     // mm, thickness (~1/8")

// --- 3. VILTROX L116T ---
viltrox_w       = 192.0;   // mm, net body width
viltrox_d       = 130.0;   // mm, net body depth (130 clears 129.38mm center bulge)
viltrox_h       = 30.0;    // mm, net body height (ghost only)
viltrox_lip     = 5.0;     // mm, retaining wall height around Viltrox

// --- 4. PRINT / FIT ---
wall            = 2.4;     // mm, structural wall thickness
clearance       = 0.40;    // mm, per-side clearance for rectangular pockets
eps             = 0.20;    // mm, boolean overlap to prevent coplanar artifacts

// --- DERIVED ---
inner_d         = dew_shield_od + bore_clearance;
outer_d         = inner_d + 2*wall;

// PTFE pocket (with clearance)
ptfe_pw         = ptfe_w + 2*clearance;
ptfe_pd         = ptfe_d + 2*clearance;

// Viltrox pocket (with clearance)
vilt_pw         = viltrox_w + 2*clearance;
vilt_pd         = viltrox_d + 2*clearance;

// Tray footprint: driven by the larger of PTFE or Viltrox + walls
tray_w          = max(ptfe_pw, vilt_pw) + 2*wall;
tray_d          = max(ptfe_pd, vilt_pd) + 2*wall;

// Transition hull height: must be self-supporting (~60 deg from horizontal).
// Worst-case lateral overhang = (tray diagonal/2 - outer_d/2).
// At 60 deg: h = overhang * tan(30) ~ 0.577 * overhang.
// Side overhang: (tray_w - outer_d)/2 = ~45mm -> needs ~26mm.
// Corner overhang: ~65mm -> needs ~38mm, but corners have adjacent
// material support, so 30mm is a practical safe value.
transition_h    = 30.0;    // mm, self-supporting cone/hull height

// Z-heights (built bottom-up)
z_transition    = coupler_h;                      // top of coupler / start of hull
z_tray_floor    = z_transition + transition_h;    // bottom of tray solid
z_ptfe_floor    = z_tray_floor;                   // PTFE recess bottom
z_ptfe_top      = z_ptfe_floor + ptfe_h;          // PTFE top = flush with tray bed
z_viltrox_floor = z_ptfe_top;                     // Viltrox rests here
z_lip_top       = z_viltrox_floor + viltrox_lip;  // top of retaining wall
tray_total_h    = (z_lip_top - z_tray_floor) + eps;
total_h         = z_lip_top;

$fn = 120;

// =====================================================================
module main() {
    if (generate_fit_test)
        fit_test_ring();
    else
        capstone();
}

// =====================================================================
//  CAPSTONE JIG
// =====================================================================
module capstone() {
    difference() {
        union() {
            // --- A) Coupler sleeve ---
            difference() {
                cylinder(h = coupler_h, d = outer_d);
                translate([0, 0, -1])
                    cylinder(h = coupler_h + 2, d = inner_d);
            }

            // --- B) Hard-stop ledge (2mm ring at top of coupler bore) ---
            //     Prevents jig from sliding too far down the dew shield.
            translate([0, 0, coupler_h - 2])
                difference() {
                    cylinder(h = 2, d = inner_d);
                    translate([0, 0, -1])
                        cylinder(h = 4, d = internal_stop_id);
                }

            // --- C) Self-supporting transition hull ---
            //     Lofts from coupler ring up to the rectangular tray base.
            //     30mm height keeps overhang angles printable without supports.
            translate([0, 0, z_transition - eps])
                hull() {
                    cylinder(h = eps, d = outer_d);
                    translate([-tray_w/2, -tray_d/2, transition_h])
                        cube([tray_w, tray_d, eps]);
                }

            // --- D) Solid tray block ---
            translate([-tray_w/2, -tray_d/2, z_tray_floor])
                cube([tray_w, tray_d, tray_total_h]);
        }

        // --- Subtract: PTFE recess (3mm deep, flush with tray bed) ---
        // The 1mm difference between ptfe_pd (128.8) and vilt_pd (130.8)
        // creates a ~1mm solid ledge on each side of the depth axis.
        // The Viltrox rests partly on this ledge, partly on the PTFE.
        translate([-ptfe_pw/2, -ptfe_pd/2, z_ptfe_floor - eps])
            cube([ptfe_pw, ptfe_pd, ptfe_h + eps]);

        // --- Subtract: Viltrox pocket (5mm deep, sits on tray bed) ---
        translate([-vilt_pw/2, -vilt_pd/2, z_viltrox_floor - eps])
            cube([vilt_pw, vilt_pd, viltrox_lip + 2*eps]);

        // --- Subtract: Central light aperture (85mm, full depth) ---
        translate([0, 0, -1])
            cylinder(h = total_h + 10, d = internal_stop_id);
    }

    // --- Ghost geometry (non-printing) ---
    if (show_ghosts) ghost_stack();
}

// =====================================================================
//  GHOST STACK -- Non-printing reference bodies
// =====================================================================
module ghost_stack() {
    vl = 0.05; // viz lift to avoid z-fighting

    // PTFE (white, translucent)
    %color([0.92, 0.92, 0.96, 0.55])
        translate([-ptfe_w/2, -ptfe_d/2, z_ptfe_floor + vl])
            cube([ptfe_w, ptfe_d, ptfe_h]);

    // Viltrox body (blue, translucent)
    %color([0.15, 0.5, 0.95, 0.30])
        translate([-viltrox_w/2, -viltrox_d/2, z_viltrox_floor + vl])
            cube([viltrox_w, viltrox_d, viltrox_h]);
}

// =====================================================================
//  FIT TEST RING -- quick print to verify dew-shield interface
// =====================================================================
module fit_test_ring() {
    stop_ring_h = 2.0;
    bore_h      = 15.0;
    difference() {
        cylinder(h = bore_h + stop_ring_h, d = outer_d);
        translate([0, 0, -1])
            cylinder(h = bore_h + 2, d = inner_d);
        translate([0, 0, bore_h])
            cylinder(h = stop_ring_h + 1, d = internal_stop_id);
    }
}

// =====================================================================
main();
