// =====================================================================
// APERTURA 75Q FLAT-FIELD CAPSTONE JIG (v7.2 - Clean Graduations)
// =====================================================================
// Design Intent: FDM manufacturing of an optical calibration jig. 
// Integrates a 98.5mm OD telescope, a 150x128x3mm PTFE diffuser, and 
// a Viltrox L116T light panel.
// =====================================================================

// --- 0. PRINT MODE CONFIGURATION ---
// Toggle these boolean flags to isolate specific geometry for test prints.
generate_fit_test     = false;  // true: prints only the 15mm base collar & taper.
generate_viltrox_test = false;  // true: prints pure hollow boundary frame.
use_prototype_walls   = false;  // true: 2.4mm tray walls. false: 4.8mm walls.
show_ghosts           = false;  // true: renders transparent bounding boxes for fitment visualization.

// =====================================================================
// --- 1. METROLOGY & FIT TOLERANCES ---
// =====================================================================

// Telescope Interface
dew_shield_od    = 98.5;    // [Measured] OD of Apertura 75Q dew shield.
felt_thickness   = 3.0;     // [Measured] Radial thickness of felt strip.
felt_height      = 12.85;   // [Measured] Height of felt strip (0.5 inches).
felt_strips      = 2;       // Number of stacked felt strips (located at entrance).
fit_compression  = 0.2;     // Tightened by +0.2mm.

// Retention Lip & Neck Integration
lip_id           = 92.5;    // Flush with dew shield interior bore.
lip_h            = 2.0;     // Structural floor separating dew shield from PTFE.
bevel_h          = 1.0;     // Entry chamfer height (45 deg).
collar_h         = 15.0;    // Straight cylindrical base for markings.

// PTFE Diffuser
ptfe_w           = 150.0;   // Long axis.
ptfe_d           = 128.0;   // Short axis.
ptfe_h           = 3.0;     // Thickness.

// Viltrox L116T Light Panel
viltrox_w        = 193.3;   // Tightened by -0.2mm from 193.5
viltrox_d        = 130.3;   // Tightened by -0.2mm from 130.5
viltrox_h        = 30.0;    // Body height.
viltrox_enclosure_h = felt_height; // 1 felt strip height.

// Viltrox Felt Retention
vilt_felt        = 3.0;     // Felt thickness per side.
vilt_fit_compression = 2.0; // Total compression per axis (1.0 mm/side).

// FDM Clearances & Stress Relief
base_wall        = 2.4;     // Base structural wall.
enclosure_wall   = use_prototype_walls ? 2.4 : 4.8; // Reinforced tray wall.
corner_r         = 5.0;     // Exterior rounding for hoop stress relief.
clearance        = 0.40;    // Per-side clearance for PTFE pocket only.
eps              = 0.20;    // Boolean overlap epsilon to prevent manifold errors.

// =====================================================================
// --- 2. DERIVED KINEMATICS & TOPOLOGY ---
// =====================================================================

// Telescope bore
inner_d          = dew_shield_od + (2 * felt_thickness) - fit_compression; // 104.3
outer_d          = inner_d + 2*base_wall;                                  // 109.1

// 45-Degree Self-Centering Taper
lip_taper_h      = (inner_d - lip_id) / 2; // 5.9mm vertical drop for 45 deg

// PTFE pocket (bare, no felt)
ptfe_pw          = ptfe_w + 2*clearance;   // 150.8
ptfe_pd          = ptfe_d + 2*clearance;   // 128.8

// Viltrox pocket (felt-lined)
vilt_pw          = viltrox_w + 2*vilt_felt - vilt_fit_compression; // 197.3
vilt_pd          = viltrox_d + 2*vilt_felt - vilt_fit_compression; // 134.3

// Tray footprint
tray_w           = max(ptfe_pw, vilt_pw) + 2*enclosure_wall; 
tray_d           = max(ptfe_pd, vilt_pd) + 2*enclosure_wall; 

// Overhang validation and 45-degree Neck Height
target_overhang_deg = 45.0; 
corner_dist      = sqrt(pow(tray_w/2, 2) + pow(tray_d/2, 2));
corner_radial    = corner_dist - outer_d/2;

transition_h     = ceil(corner_radial / tan(target_overhang_deg)); // ~67.0
prod_neck_h      = collar_h + transition_h; // ~82.0 mm
test_neck_h      = collar_h; // 15.0 mm pure collar    
neck_h           = generate_fit_test ? test_neck_h : prod_neck_h;

// Z-Heights
z_base           = 0.0;
z_transition     = collar_h;
z_lip_bottom     = neck_h;                   
z_ptfe_floor     = z_lip_bottom + lip_h;     
z_viltrox_floor  = z_ptfe_floor + ptfe_h;
z_enclosure_top  = z_viltrox_floor + viltrox_enclosure_h;

total_h          = z_enclosure_top;

// Global Facet Resolution
$fn = 120;

// =====================================================================
// --- 3. MODULES & GEOMETRY OPERATIONS ---
// =====================================================================

module main() {
    if (generate_fit_test && generate_viltrox_test) {
        // Output offset to prevent bounding box overlap during simultaneous prints
        fit_test_ring();
        translate([0, 0, 0]) viltrox_test_tray();
    } else if (generate_fit_test) {
        fit_test_ring();
    } else if (generate_viltrox_test) {
        viltrox_test_tray();
    } else {
        capstone();
    }
}

module rounded_cube(dim, r) {
    hull() {
        for(x = [-dim[0]/2+r, dim[0]/2-r])
            for(y = [-dim[1]/2+r, dim[1]/2-r])
                translate([x, y, 0]) cylinder(r=r, h=dim[2], $fn=60);
    }
}

module angle_markings() {
    // 45-degree major, 15-degree minor [v7.2]
    for(a = [0 : 15 : 345]) {
        rotate([0, 0, a]) {
            if (a % 45 == 0) {
                // Major Tick (45 deg)
                translate([outer_d/2 - 0.6, -0.4, 3.0])
                    cube([2.0, 0.8, 5.0]);
                
                // Text Alignment
                translate([outer_d/2 - 0.6, 0, 11.0])
                    rotate([90, 0, 90])
                    linear_extrude(2.0)
                    text(str(a, "°"), size=3.5, halign="center", valign="center", font="Liberation Sans:style=Bold");
            } else {
                // Minor Tick (15 deg)
                translate([outer_d/2 - 0.6, -0.3, 3.0])
                    cube([2.0, 0.6, 3.0]);
            }
        }
    }
}

module capstone() {
    difference() {
        union() {
            // A) Straight Base Collar
            cylinder(h = collar_h, d = outer_d);
            
            // B) Integrated Transition Hull
            translate([0, 0, collar_h - eps])
            hull() {
                cylinder(h = eps, d = outer_d);
                translate([0, 0, transition_h])
                    rounded_cube([tray_w, tray_d, eps], corner_r);
            }
            
            // C) Upper Enclosure Shell
            translate([0, 0, z_lip_bottom])
                rounded_cube([tray_w, tray_d, total_h - z_lip_bottom], corner_r);
        }

        // 1. Full-height aperture at lip_id
        translate([0, 0, -1]) 
            cylinder(h = total_h + 2, d = lip_id);
            
        // 2. Telescope Bore (Straight wall section)
        translate([0, 0, -1]) 
            cylinder(h = z_lip_bottom - lip_taper_h + 1, d = inner_d);
            
        // 3. Support-Free 45-Degree Self-Centering Taper
        translate([0, 0, z_lip_bottom - lip_taper_h]) 
            cylinder(h = lip_taper_h + eps, d1 = inner_d, d2 = lip_id);
            
        // 4. Entry Bevel
        translate([0, 0, -eps]) 
            cylinder(h = bevel_h + eps, d1 = inner_d + 2*bevel_h, d2 = inner_d);
        
        // 5. PTFE Pocket
        translate([-ptfe_pw/2, -ptfe_pd/2, z_ptfe_floor - eps])
            cube([ptfe_pw, ptfe_pd, ptfe_h + eps]);
            
        // 6. Viltrox Pocket
        translate([-vilt_pw/2, -vilt_pd/2, z_viltrox_floor - eps])
            cube([vilt_pw, vilt_pd, viltrox_enclosure_h + 2*eps]);
            
        // 7. Outer Graduated Markings (Embossed on straight collar)
        angle_markings();
    }
    if (show_ghosts) ghost_stack();
}

module fit_test_ring() {
    difference() {
        cylinder(h = neck_h + lip_h, d = outer_d);
        translate([0, 0, -1]) 
            cylinder(h = neck_h + lip_h + 2, d = lip_id);
        translate([0, 0, -1]) 
            cylinder(h = neck_h - lip_taper_h + 1, d = inner_d);
        translate([0, 0, neck_h - lip_taper_h]) 
            cylinder(h = lip_taper_h + eps, d1 = inner_d, d2 = lip_id);
        translate([0, 0, -eps]) 
            cylinder(h = bevel_h + eps, d1 = inner_d + 2*bevel_h, d2 = inner_d);
        angle_markings();
    }
}

module viltrox_test_tray() {
    difference() {
        translate([0, 0, 0])
            rounded_cube([tray_w, tray_d, viltrox_enclosure_h], corner_r);
        translate([-vilt_pw/2, -vilt_pd/2, -eps])
            cube([vilt_pw, vilt_pd, viltrox_enclosure_h + 2*eps]);
    }
}

module ghost_stack() {
    vl = 0.05;
    %color([0.92, 0.92, 0.96, 0.55])
        translate([-ptfe_w/2, -ptfe_d/2, z_ptfe_floor + vl])
            cube([ptfe_w, ptfe_d, ptfe_h]);
    %color([0.15, 0.5, 0.95, 0.30])
        translate([-viltrox_w/2, -viltrox_d/2, z_viltrox_floor + vl])
            cube([viltrox_w, viltrox_d, viltrox_h]);
}

// Execute geometry generation
main();