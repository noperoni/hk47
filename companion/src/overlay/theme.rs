use serde::Deserialize;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use super::sprite::{AnimState, Direction, SpriteSheet};

// Embedded default sprite sheets (compiled into binary).
const DEFAULT_IDLE_PNG: &[u8] = include_bytes!("../assets/sprites/idle.png");
const DEFAULT_IDLE_FRAMES: usize = 8;

/// On-disk theme manifest, deserialized from theme.toml.
#[derive(Debug, Deserialize)]
struct ThemeManifest {
    #[serde(default)]
    meta: ThemeMeta,
    /// Where the figure's feet sit, where the floor is, and how big the ring is.
    #[serde(default)]
    geometry: Geometry,
    /// Per-state animation definitions. Keys: "idle", "idle_alt", "scan_l",
    /// "scan_r", which are the four beats of the idle rotation and all there is.
    #[serde(default)]
    animations: HashMap<String, AnimationDef>,
    /// Optional static diorama backdrop drawn behind the sprite.
    #[serde(default)]
    backdrop: Option<BackdropDef>,
    /// Wall consoles in the backdrop that double as attention counters.
    /// Keys: "question", "permission", "waiting".
    #[serde(default)]
    readouts: HashMap<String, Readout>,
}

/// One of the corridor's own bezelled screens, repurposed as a counter.
///
/// The room is already full of consoles, so a live counter repaints the glass
/// inside one of them and a dead counter leaves the backdrop byte-identical to
/// how it is painted. Position is the identity: a given panel always means the
/// same counter, which is why nothing needs an icon stamped into eleven pixels.
///
/// A theme with no `[readouts]` table simply shows no counts. That is the right
/// failure: the rects are measured against one specific backdrop, and guessing
/// at them for a pack that never declared any would light up whatever happened
/// to be painted at those coordinates.
#[derive(Debug, Clone, Copy, Deserialize)]
pub struct Readout {
    /// Glass rect in backdrop pixels, `[x0, y0, x1, y1]`, x1/y1 exclusive.
    pub rect: [f64; 4],
    /// Lit colour as RGB 0-255. The glass, rim and bloom are all derived from it.
    pub colour: [f64; 3],
}

impl Readout {
    /// The lit colour as cairo's 0.0-1.0 components.
    pub fn tint(&self) -> [f64; 3] {
        [self.colour[0] / 255.0, self.colour[1] / 255.0, self.colour[2] / 255.0]
    }
}

#[derive(Debug, Default, Deserialize)]
struct ThemeMeta {
    #[serde(default = "default_name")]
    name: String,
    #[serde(default)]
    author: String,
}

/// Where the theme's art expects to be drawn. These four numbers used to be
/// `const`s in main.rs, which forced every pack to be built to match whatever
/// the previous pack happened to be; a theme that owns them can change its own
/// framing without a recompile. The defaults reproduce the old constants
/// exactly, so a theme.toml with no `[geometry]` table renders as it always did.
#[derive(Debug, Clone, Copy, Deserialize)]
pub struct Geometry {
    /// y within a sprite frame where the soles sit.
    #[serde(default = "default_feet_y")]
    pub feet_y: f64,
    /// y within the backdrop where those soles must land.
    #[serde(default = "default_floor_y")]
    pub floor_y: f64,
    /// Figure size as a multiple of the configured sprite size. 1.0 means the
    /// art is drawn at its native pixel density with no resampling at all.
    #[serde(default = "default_geom_scale")]
    pub scale: f64,
    /// Width of the backdrop's frame line. The scan beam is clipped to the
    /// interior this leaves, the frame being an object in front of the scene
    /// rather than part of the room being scanned.
    #[serde(default = "default_border")]
    pub border: u32,
}

impl Default for Geometry {
    fn default() -> Self {
        Self {
            feet_y: default_feet_y(),
            floor_y: default_floor_y(),
            scale: default_geom_scale(),
            border: default_border(),
        }
    }
}

#[derive(Debug, Deserialize)]
struct AnimationDef {
    /// Single-file strip (non-directional). Loaded as Direction::S.
    #[serde(default)]
    file: Option<String>,
    /// Pattern like "a-{dir}.png", with {dir} replaced per direction. Takes precedence over file.
    #[serde(default)]
    file_pattern: Option<String>,
    /// Frames per strip. Omit to auto-detect from square (NxN) frames, which lets
    /// each direction carry its own length without per-file bookkeeping.
    #[serde(default)]
    frames: Option<usize>,
    /// Tick divisor: higher = slower. 0 = frozen.
    #[serde(default = "default_divisor")]
    tick_divisor: u64,
    /// First frame of the loop. Frames 0..loop_start play once on state entry;
    /// the animation then wraps back here instead of frame 0.
    #[serde(default)]
    loop_start: Option<usize>,
    /// Per-frame hold points: the animation pauses on these frames for the given
    /// number of timer ticks before advancing. At 24 fps, 72 ticks ≈ 3 seconds.
    #[serde(default)]
    holds: Vec<FrameHold>,
}

#[derive(Debug, Deserialize)]
struct FrameHold {
    frame: usize,
    ticks: u64,
    /// If set, the animation mini-loops [loop_from..=frame] during the hold instead of freezing.
    #[serde(default)]
    loop_from: Option<usize>,
}

/// Runtime hold descriptor stored in StateAnimation.
pub struct HoldDef {
    pub ticks: u64,
    pub loop_from: Option<usize>,
}

/// Optional diorama backdrop: a single static image drawn behind the sprite.
#[derive(Debug, Deserialize)]
struct BackdropDef {
    /// Path (relative to the theme dir) to the backdrop PNG.
    file: String,
}

fn default_name() -> String {
    "Unnamed".to_string()
}
fn default_divisor() -> u64 {
    1
}
// The pre-2026-09-10 main.rs constants, kept as defaults so an old pack is unchanged.
fn default_feet_y() -> f64 {
    93.0
}
fn default_floor_y() -> f64 {
    133.0
}
fn default_geom_scale() -> f64 {
    1.35
}
fn default_border() -> u32 {
    16
}
fn holds_map(holds: &[FrameHold]) -> HashMap<usize, HoldDef> {
    holds.iter().map(|h| (h.frame, HoldDef { ticks: h.ticks, loop_from: h.loop_from })).collect()
}

/// A single animation state's loaded assets + timing.
pub struct StateAnimation {
    pub sheet: SpriteSheet,
    pub tick_divisor: u64,
    /// Frame index the animation wraps to after completing its first pass (intro plays once).
    pub loop_start: usize,
    /// Frames on which the animation holds: maps frame index → HoldDef (duration + optional mini-loop).
    pub holds: HashMap<usize, HoldDef>,
}

/// A fully loaded theme: per-(state, direction) sprite sheets + animation parameters.
pub struct Theme {
    pub name: String,
    pub author: String,
    geometry: Geometry,
    states: HashMap<(AnimState, Direction), StateAnimation>,
    backdrop: Option<SpriteSheet>,
    readouts: HashMap<String, Readout>,
}

impl Theme {
    /// Get the animation data for a given state + direction.
    ///
    /// Fallback chain: (state, dir) → (state, S) → (Idle, S).
    /// The constructor invariant guarantees (Idle, S) is always present.
    pub fn state_anim_dir(&self, state: AnimState, dir: Direction) -> &StateAnimation {
        self.states
            .get(&(state, dir))
            .or_else(|| self.states.get(&(state, Direction::S)))
            .or_else(|| self.states.get(&(AnimState::Idle, Direction::S)))
            .expect("Theme invariant: (Idle, S) always present")
    }

    /// The static diorama backdrop drawn behind the sprite, if this theme defines one.
    pub fn backdrop(&self) -> Option<&SpriteSheet> {
        self.backdrop.as_ref()
    }

    /// Where this theme's art expects to be drawn: see `Geometry`.
    pub fn geometry(&self) -> Geometry {
        self.geometry
    }

    /// The wall console this theme assigns to a counter, if it declares one.
    pub fn readout(&self, key: &str) -> Option<Readout> {
        self.readouts.get(key).copied()
    }
}

/// Directory where user themes live: ~/.config/hk47/sprites/
pub fn themes_dir() -> PathBuf {
    crate::config::config_dir().join("sprites")
}

/// Load a theme by name. Fallback chain:
///   1. Try ~/.config/hk47/sprites/{name}/theme.toml
///   2. If name is "default" or loading fails, use embedded default
pub fn load_theme(name: &str) -> Theme {
    if name != "default" {
        let theme_dir = themes_dir().join(name);
        match try_load_from_dir(&theme_dir) {
            Ok(theme) => {
                eprintln!("info: loaded theme '{}' by {}", theme.name, theme.author);
                return theme;
            }
            Err(e) => {
                eprintln!(
                    "warn: failed to load theme '{}': {}, falling back to default",
                    name, e
                );
            }
        }
    }
    load_embedded_default()
}

/// Attempt to load a theme from a directory on disk.
fn try_load_from_dir(theme_dir: &Path) -> Result<Theme, String> {
    let manifest_path = theme_dir.join("theme.toml");
    let manifest_str = fs::read_to_string(&manifest_path)
        .map_err(|e| format!("cannot read {}: {}", manifest_path.display(), e))?;
    let manifest: ThemeManifest =
        toml::from_str(&manifest_str).map_err(|e| format!("invalid theme.toml: {}", e))?;

    let mut states: HashMap<(AnimState, Direction), StateAnimation> = HashMap::new();

    for (state_key, anim_def) in &manifest.animations {
        let Some(anim_state) = parse_state_key(state_key) else {
            eprintln!("warn: unknown animation state '{}', skipping", state_key);
            continue;
        };
        if anim_def.frames == Some(0) {
            eprintln!(
                "warn: state '{}' has frames=0, skipping (will fall back)",
                state_key
            );
            continue;
        }

        match (&anim_def.file_pattern, &anim_def.file) {
            (Some(pattern), _) => {
                // Directional: load a strip per facing direction.
                for &facing in &Direction::ALL {
                    let filename = pattern.replace("{dir}", facing.to_abbrev());
                    let png_path = theme_dir.join(&filename);
                    match fs::read(&png_path) {
                        Ok(bytes) => match SpriteSheet::from_png_bytes(&bytes, anim_def.frames) {
                            Ok(sheet) => {
                                states.insert(
                                    (anim_state, facing),
                                    StateAnimation {
                                        sheet,
                                        tick_divisor: anim_def.tick_divisor,
                                        loop_start: anim_def.loop_start.unwrap_or(0),
                                        holds: holds_map(&anim_def.holds),
                                    },
                                );
                            }
                            Err(e) => {
                                eprintln!(
                                    "warn: failed to decode {}: {}",
                                    png_path.display(),
                                    e
                                );
                            }
                        },
                        Err(_) => {
                            // Missing direction: silently skip; fallback chain covers it.
                        }
                    }
                }
            }
            (None, Some(file)) => {
                // Single-file strip, stored as Direction::S, other dirs fall back to it.
                let png_path = theme_dir.join(file);
                match fs::read(&png_path) {
                    Ok(bytes) => match SpriteSheet::from_png_bytes(&bytes, anim_def.frames) {
                        Ok(sheet) => {
                            states.insert(
                                (anim_state, Direction::S),
                                StateAnimation {
                                    sheet,
                                    tick_divisor: anim_def.tick_divisor,
                                    loop_start: anim_def.loop_start.unwrap_or(0),
                                    holds: holds_map(&anim_def.holds),
                                },
                            );
                        }
                        Err(e) => {
                            eprintln!(
                                "warn: failed to decode {}: {}, will fall back",
                                png_path.display(),
                                e
                            );
                        }
                    },
                    Err(e) => {
                        eprintln!(
                            "warn: cannot load {}: {}, will fall back",
                            png_path.display(),
                            e
                        );
                    }
                }
            }
            (None, None) => {
                eprintln!(
                    "warn: state '{}' has neither file nor file_pattern, skipping",
                    state_key
                );
            }
        }
    }

    // Ensure (Idle, S) exists: it's the ultimate fallback for all missing combinations.
    states.entry((AnimState::Idle, Direction::S)).or_insert_with(|| {
        eprintln!("warn: theme missing idle state, using embedded default");
        embedded_idle_animation()
    });

    // Ensure (state, S) exists for each required non-idle state.
    // The fallback chain then covers all other directions automatically.
    // A theme missing one of these still runs: the state reads as idle and the
    // warning line says which strip is absent.
    for &required in &[AnimState::IdleAlt, AnimState::ScanL, AnimState::ScanR] {
        if !states.contains_key(&(required, Direction::S)) {
            eprintln!(
                "warn: theme missing {:?}/S state, falling back to idle",
                required
            );
            let idle_sheet = states[&(AnimState::Idle, Direction::S)].sheet.clone();
            states.insert(
                (required, Direction::S),
                StateAnimation {
                    sheet: idle_sheet,
                    tick_divisor: default_tick_divisor_for(required),
                    loop_start: 0,
                    holds: HashMap::new(),
                },
            );
        }
    }

    // Optional diorama backdrop: a single static frame drawn behind the sprite.
    // A decode/read failure warns and falls back to no backdrop (sprite renders alone).
    let backdrop = manifest.backdrop.as_ref().and_then(|bd| {
        let png_path = theme_dir.join(&bd.file);
        match fs::read(&png_path) {
            Ok(bytes) => match SpriteSheet::from_png_bytes(&bytes, Some(1)) {
                Ok(sheet) => Some(sheet),
                Err(e) => {
                    eprintln!("warn: failed to decode backdrop {}: {}", png_path.display(), e);
                    None
                }
            },
            Err(e) => {
                eprintln!("warn: cannot load backdrop {}: {}", png_path.display(), e);
                None
            }
        }
    });

    Ok(Theme {
        name: manifest.meta.name,
        author: manifest.meta.author,
        geometry: manifest.geometry,
        states,
        backdrop,
        readouts: manifest.readouts,
    })
}

/// Build theme from compiled-in assets.
fn load_embedded_default() -> Theme {
    // Embedded assets are verified at build time via include_bytes!, so decode failure
    // here means the shipped binary is broken, which is a build-time bug, not runtime input.
    let mut states = HashMap::new();
    states.insert((AnimState::Idle, Direction::S), embedded_idle_animation());
    Theme {
        name: "default".to_string(),
        author: "HK-47".to_string(),
        geometry: Geometry::default(),
        states,
        backdrop: None,
        // No backdrop means no corridor and therefore no consoles to light.
        readouts: HashMap::new(),
    }
}

/// Load just the embedded idle animation (fallback for missing states in disk themes).
fn embedded_idle_animation() -> StateAnimation {
    StateAnimation {
        sheet: SpriteSheet::from_png_bytes(DEFAULT_IDLE_PNG, Some(DEFAULT_IDLE_FRAMES))
            .expect("embedded idle asset must be valid"),
        tick_divisor: 3,
        loop_start: 0,
        holds: HashMap::new(),
    }
}

fn parse_state_key(key: &str) -> Option<AnimState> {
    match key {
        "idle" => Some(AnimState::Idle),
        "idle_alt" => Some(AnimState::IdleAlt),
        "scan_l" => Some(AnimState::ScanL),
        "scan_r" => Some(AnimState::ScanR),
        _ => None,
    }
}

fn default_tick_divisor_for(state: AnimState) -> u64 {
    match state {
        AnimState::Idle | AnimState::IdleAlt => 3,
        AnimState::ScanL | AnimState::ScanR => 3,
    }
}
