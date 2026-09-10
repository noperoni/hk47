use gtk4::cairo;
use gtk4::gdk;
use gtk4::prelude::*;
use std::rc::Rc;

use super::theme::Theme;

/// Animation states for the sprite.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum AnimState {
    Idle,
    IdleAlt,
    Attentive,
    Thinking,
    /// Something HK-47 was asked to do failed. Entered from UiEvent::Error and
    /// left again by the next toggle, Escape or prompt, so it is transient by
    /// construction and never a state he can be stranded in.
    Error,
    /// Head turned 45° to the viewer's left, sweeping the room with the scanner.
    /// Part of the idle rotation, not a state the UI ever asks for.
    ScanL,
    /// The same, to the viewer's right.
    ScanR,
}

/// The 8 cardinal + intercardinal directions HK-47 can face.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Direction {
    S,
    SE,
    E,
    NE,
    N,
    NW,
    W,
    SW,
}

impl Direction {
    pub const ALL: [Direction; 8] = [
        Direction::S,
        Direction::SE,
        Direction::E,
        Direction::NE,
        Direction::N,
        Direction::NW,
        Direction::W,
        Direction::SW,
    ];

    pub fn to_abbrev(self) -> &'static str {
        match self {
            Direction::S => "s",
            Direction::SE => "se",
            Direction::E => "e",
            Direction::NE => "ne",
            Direction::N => "n",
            Direction::NW => "nw",
            Direction::W => "w",
            Direction::SW => "sw",
        }
    }

}

/// A loaded sprite sheet: a single image containing N equally-sized frames laid out horizontally,
/// pre-converted to a Cairo surface for efficient per-frame rendering.
#[derive(Clone)]
pub struct SpriteSheet {
    surface: cairo::ImageSurface,
    frame_width: i32,
    frame_height: i32,
    frame_count: usize,
    /// Measurements taken once at load time — see `FrameMetrics`.
    metrics: Vec<FrameMetrics>,
}

/// What a frame's pixels tell us about where things are on the figure. Both of
/// these drive procedural effects that have to follow the art rather than sit at
/// fixed offsets, so they are measured rather than configured.
#[derive(Clone, Copy)]
pub struct FrameMetrics {
    /// (centre x, width) of the figure's contact with the floor, frame-local px.
    /// Width is 0 for an empty frame, which callers read as "draw no shadow".
    pub footprint: (f64, f64),
    /// Centroid of the photoreceptors, frame-local px. The scan beam starts here,
    /// so it keeps its origin when the head turns.
    pub eye: (f64, f64),
}

/// Rows above a frame's lowest opaque row that count as "feet" when measuring
/// the footprint. Wide enough to catch both soles mid-stride, short enough to
/// exclude the knees.
const FOOTPRINT_ROWS: i32 = 12;
/// Alpha below this is antialiasing fringe, not figure.
const FOOTPRINT_ALPHA: u8 = 24;
/// The photoreceptors are (206,48,40) — a far harder red than the rust plating,
/// which sits around (140,82,48). Thresholding on the red-to-green and
/// red-to-blue ratios separates the two cleanly: on the idle art this matches
/// exactly the eight eye pixels and nothing else on the body.
const EYE_MIN_RED: u8 = 120;
const EYE_MIN_ALPHA: u8 = 200;

/// Measure every frame in one pass over the downloaded BGRA buffer.
///
/// The footprint is deliberately not the full bounding box: in the combat strip
/// that box is 112px wide against idle's 49, and a shadow sized from it would
/// balloon out from under his feet the moment he shoulders the rifle. The
/// footprint tracks the stance instead, which is what actually touches the deck.
fn measure_frames(
    data: &[u8],
    stride: usize,
    height: i32,
    frame_width: i32,
    frame_count: usize,
) -> Vec<FrameMetrics> {
    let at = |x: i32, y: i32| -> (u8, u8, u8, u8) {
        let i = y as usize * stride + x as usize * 4;
        (data[i + 2], data[i + 1], data[i], data[i + 3]) // B8G8R8A8 in memory order
    };
    (0..frame_count)
        .map(|i| {
            let x0 = i as i32 * frame_width;
            let x1 = x0 + frame_width;
            let default_eye = (frame_width as f64 / 2.0, height as f64 * 0.25);

            let opaque_row = |y: i32| (x0..x1).any(|x| at(x, y).3 > FOOTPRINT_ALPHA);
            let Some(bottom) = (0..height).rev().find(|&y| opaque_row(y)) else {
                return FrameMetrics { footprint: (frame_width as f64 / 2.0, 0.0), eye: default_eye };
            };

            let top = (bottom - FOOTPRINT_ROWS + 1).max(0);
            let feet = (top..=bottom)
                .flat_map(|y| (x0..x1).map(move |x| (x, y)))
                .filter(|&(x, y)| at(x, y).3 > FOOTPRINT_ALPHA)
                .map(|(x, _)| x);
            let (left, right) = feet.fold((i32::MAX, i32::MIN), |(lo, hi), x| (lo.min(x), hi.max(x)));
            let footprint = if left > right {
                (frame_width as f64 / 2.0, 0.0)
            } else {
                ((left + right + 1) as f64 / 2.0 - x0 as f64, (right - left + 1) as f64)
            };

            let mut sx = 0i64;
            let mut sy = 0i64;
            let mut n = 0i64;
            for y in 0..height {
                for x in x0..x1 {
                    let (r, g, b, a) = at(x, y);
                    if a >= EYE_MIN_ALPHA
                        && r >= EYE_MIN_RED
                        && (g as u32) * 22 < (r as u32) * 10
                        && (b as u32) * 2 < r as u32
                    {
                        sx += (x - x0) as i64;
                        sy += y as i64;
                        n += 1;
                    }
                }
            }
            let eye = if n == 0 {
                default_eye
            } else {
                (sx as f64 / n as f64 + 0.5, sy as f64 / n as f64 + 0.5)
            };

            FrameMetrics { footprint, eye }
        })
        .collect()
}

impl SpriteSheet {
    /// Load a sprite sheet from PNG bytes.
    ///
    /// Converts the GDK texture to a Cairo ImageSurface at load time so that
    /// per-frame rendering is a cheap blit rather than a texture download.
    ///
    /// `frame_count`:
    ///   - `Some(n)` — strip has exactly n frames; width must divide evenly.
    ///   - `None` — auto-detect assuming square frames (frame_count = width / height).
    ///
    /// Returns `Err` on invalid input (zero/indivisible frames, PNG decode failure,
    /// surface creation failure) so callers can warn and fall back. For trusted inputs
    /// (embedded assets verified at compile time via `include_bytes!`), unwrap with
    /// `.expect("embedded assets must be valid")`.
    pub fn from_png_bytes(bytes: &[u8], frame_count: Option<usize>) -> Result<Self, String> {
        let gbytes = gtk4::glib::Bytes::from(bytes);
        let texture =
            gdk::Texture::from_bytes(&gbytes).map_err(|e| format!("PNG decode failed: {e}"))?;

        let total_width = texture.width();
        let height = texture.height();

        let frame_count = match frame_count {
            Some(0) => return Err("frame_count must be >= 1".to_string()),
            Some(n) => n,
            None => {
                // Auto-detect: frames are square (frame_width == frame_height).
                if height <= 0 || total_width % height != 0 {
                    return Err(format!(
                        "cannot auto-detect frames: {total_width}x{height} is not a whole number of square frames"
                    ));
                }
                (total_width / height) as usize
            }
        };

        if total_width % (frame_count as i32) != 0 {
            return Err(format!(
                "sheet width {total_width} not divisible by frame count {frame_count} — likely a WIP export"
            ));
        }
        let frame_width = total_width / frame_count as i32;

        // Download texture pixels into a Cairo-compatible byte buffer.
        // B8g8r8a8Premultiplied matches Cairo's ARgb32 on little-endian (x86).
        let mut downloader = gdk::TextureDownloader::new(&texture);
        downloader.set_format(gdk::MemoryFormat::B8g8r8a8Premultiplied);
        let (pixel_bytes, stride) = downloader.download_bytes();

        let metrics = measure_frames(&pixel_bytes, stride, height, frame_width, frame_count);

        let data: Vec<u8> = pixel_bytes[..].to_vec();
        let surface = cairo::ImageSurface::create_for_data(
            data,
            cairo::Format::ARgb32,
            total_width,
            height,
            stride as i32,
        )
        .map_err(|e| format!("cairo surface creation failed: {e}"))?;

        Ok(Self {
            surface,
            frame_width,
            frame_height: height,
            frame_count,
            metrics,
        })
    }

    pub fn frame_count(&self) -> usize {
        self.frame_count
    }

    /// Load-time measurements for one frame; see `FrameMetrics`.
    pub fn metrics(&self, frame_index: usize) -> FrameMetrics {
        self.metrics.get(frame_index).copied().unwrap_or(FrameMetrics {
            footprint: (self.frame_width as f64 / 2.0, 0.0),
            eye: (self.frame_width as f64 / 2.0, self.frame_height as f64 * 0.25),
        })
    }

    pub fn frame_width(&self) -> i32 {
        self.frame_width
    }

    pub fn frame_height(&self) -> i32 {
        self.frame_height
    }
}

/// Pacing motion (Thinking state): HK-47 walks back and forth across the study
/// floor instead of pondering in place. Tuned in native backdrop pixels so the
/// draw loop can scale them by the diorama factor.
/// Half-width of the pacing path each side of centre — bounded to the clear rug
/// between the reading lamp (left) and the fireplace (right).
const PACE_HALF_RANGE: f64 = 30.0;
/// Horizontal travel per timer tick (native px). Tuned against the walk cadence
/// so his feet don't slide; eye-checked on the diorama.
const PACE_PER_TICK: f64 = 1.0;

/// The idle rotation: what he does when nobody is asking him for anything.
///
/// Each beat is a pose and a facing. He settles into it over the strip's own
/// frames, then holds it dead still for the rest of the beat, then cuts to the
/// next one. The cut is deliberate: a droid re-aims, it does not ease.
///
/// The previous behaviour looped a nine-frame strip end to end at three ticks a
/// frame, so he re-performed the whole idle roughly once a second, which reads
/// as a nervous tic rather than as a machine standing in a corridor.
/// His body never rotates here. An earlier version cut to the 8-way sheet for
/// the scans, which pivoted the whole droid like a turret; only the head turns,
/// and the strip for it is spliced from those same rotations at build time.
const IDLE_BEATS: [(AnimState, Direction); 4] = [
    (AnimState::Idle, Direction::S),    // stand
    (AnimState::ScanL, Direction::S),   // head 45° left, sweep the room
    (AnimState::IdleAlt, Direction::S), // shift onto the other leg
    (AnimState::ScanR, Direction::S),   // head 45° right, sweep again
];
/// Beat length in timer ticks. At the default 24 fps this is 10 to 15 seconds,
/// randomised per beat so the rotation never becomes a metronome.
const IDLE_DWELL_MIN_TICKS: u64 = 240;
const IDLE_DWELL_MAX_TICKS: u64 = 360;

/// One full top-to-bottom traverse of the scan beam, in ticks: 3 seconds at the
/// default 24 fps, which is slow enough to read as deliberate.
const SCAN_SWEEP_TICKS: f64 = 72.0;
/// Emitter spin-up and spin-down, in ticks.
const SCAN_RAMP_TICKS: f64 = 9.0;
/// Ticks the head is given to finish turning before the beam fires.
const SCAN_HEAD_TICKS: f64 = 6.0;
/// Ticks the head holds its turn after the emitter has gone dark, before the
/// beat hands over and he faces front again. Half a second, per Master.
const SCAN_TAIL_TICKS: u64 = 12;
/// Ignition and shutdown flash at the emitter, in ticks.
const SCAN_FLASH_TICKS: f64 = 5.0;

/// A scan sweep in progress. The Animator owns the timing; the draw loop owns
/// the geometry, because only it knows where the room's walls are on screen.
pub struct Beam {
    /// -1.0 sweeps to the viewer's left, +1.0 to the right.
    pub side: f64,
    /// Vertical sweep position: 0.0 at the top of the room, 1.0 at the bottom.
    pub t: f64,
    /// 0.0 to 1.0, ramping at both ends of the beat.
    pub intensity: f64,
    /// 0.0 to 1.0, non-zero only for the few ticks either side of ignition and
    /// shutdown. Drives the emitter bloom and a brief lift in the beam itself.
    pub flash: f64,
}

/// Manages the current animation state and frame index.
/// Reads per-state timing and frame counts from the loaded Theme.
pub struct Animator {
    state: AnimState,
    direction: Direction,
    frame: usize,
    tick_counter: u64,
    hold_remaining: u64,
    hold_loop_from: Option<usize>,
    hold_loop_end: usize,
    /// Index into IDLE_BEATS of the pose he is currently holding.
    idle_beat: usize,
    /// Ticks left in the current idle beat, and how long it was to begin with.
    idle_dwell: u64,
    idle_dwell_total: u64,
    /// xorshift64 state, seeded at launch. A whole RNG crate for four numbers a
    /// minute would be an indulgence.
    rng: u64,
    /// Pacing horizontal offset from centre, in native backdrop px (Thinking only).
    pace_x: f64,
    /// Pacing travel direction: +1 = moving east (right), -1 = moving west (left).
    /// The walk art faces west, so +1 is drawn mirrored.
    pace_sign: f64,
    theme: Rc<Theme>,
}

impl Animator {
    pub fn new(theme: Rc<Theme>) -> Self {
        // Seed from startup nanos so two instances don't scan in lockstep.
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.subsec_nanos())
            .unwrap_or(0);
        let mut anim = Self {
            state: AnimState::Idle,
            direction: Direction::S,
            frame: 0,
            tick_counter: 0,
            hold_remaining: 0,
            hold_loop_from: None,
            hold_loop_end: 0,
            idle_beat: 0,
            idle_dwell: 0,
            idle_dwell_total: 0,
            rng: (nanos as u64) | 1, // xorshift dies on a zero seed
            pace_x: 0.0,
            pace_sign: -1.0,
            theme,
        };
        anim.enter_idle_beat(anim.rng as usize % IDLE_BEATS.len());
        anim
    }

    /// Next beat length, in ticks, drawn from [MIN, MAX].
    fn next_dwell(&mut self) -> u64 {
        self.rng ^= self.rng << 13;
        self.rng ^= self.rng >> 7;
        self.rng ^= self.rng << 17;
        IDLE_DWELL_MIN_TICKS + self.rng % (IDLE_DWELL_MAX_TICKS - IDLE_DWELL_MIN_TICKS + 1)
    }

    /// Cut to one beat of the idle rotation and start its dwell.
    fn enter_idle_beat(&mut self, beat: usize) {
        let (state, direction) = IDLE_BEATS[beat];
        self.idle_beat = beat;
        self.idle_dwell = self.next_dwell();
        self.idle_dwell_total = self.idle_dwell;
        self.state = state;
        self.direction = direction;
        self.frame = 0;
        self.tick_counter = 0;
        self.hold_remaining = 0;
        self.hold_loop_from = None;
    }

    /// True while he is in the idle rotation, whichever pose it is on.
    fn is_idle(&self) -> bool {
        matches!(
            self.state,
            AnimState::Idle | AnimState::IdleAlt | AnimState::ScanL | AnimState::ScanR
        )
    }

    /// The scan beam's state this tick, or None when he is not scanning.
    ///
    /// The sweep starts at the middle heading down and bounces, so a full
    /// down-and-back-up is two traverses. Intensity ramps at both ends of the
    /// beat: the emitter spins up after the head has finished turning, and spins
    /// down before he faces front again, so it never simply blinks out.
    pub fn beam(&self) -> Option<Beam> {
        let side = match self.state {
            AnimState::ScanL => -1.0,
            AnimState::ScanR => 1.0,
            _ => return None,
        };
        let elapsed = self.idle_dwell_total.saturating_sub(self.idle_dwell) as f64;
        if elapsed < SCAN_HEAD_TICKS {
            return None;
        }
        let s = elapsed - SCAN_HEAD_TICKS;
        // The emitter dies SCAN_TAIL_TICKS early. Those ticks are the held turn:
        // beam() returns None through them, so tick() stops asking for redraws
        // and he simply stands there with his head still round.
        let remaining = self.idle_dwell.saturating_sub(SCAN_TAIL_TICKS) as f64;
        let intensity = (s / SCAN_RAMP_TICKS)
            .min(remaining / SCAN_RAMP_TICKS)
            .clamp(0.0, 1.0);
        let ignite = (1.0 - s / SCAN_FLASH_TICKS).clamp(0.0, 1.0);
        if intensity <= 0.0 && ignite <= 0.0 {
            return None;
        }
        let douse = (1.0 - remaining / SCAN_FLASH_TICKS).clamp(0.0, 1.0);
        let flash = ignite.max(douse);
        let phase = (s / SCAN_SWEEP_TICKS + 0.5) % 2.0;
        let t = if phase <= 1.0 { phase } else { 2.0 - phase };
        Some(Beam { side, t, intensity, flash })
    }

    #[allow(dead_code)] // public introspection for future debug overlay / state-aware UI
    pub fn state(&self) -> AnimState {
        self.state
    }

    /// Get the SpriteSheet for the current state + direction.
    pub fn current_sheet(&self) -> &SpriteSheet {
        &self.theme.state_anim_dir(self.state, self.direction).sheet
    }

    /// Transition to a new animation state, resetting to frame 0.
    ///
    /// Passing Idle hands him back to the idle rotation, resuming at the *next*
    /// beat rather than restarting it, so returning from a task does not snap
    /// him into the same pose every single time.
    pub fn set_state(&mut self, state: AnimState) {
        if state == AnimState::Idle {
            self.enter_idle_beat((self.idle_beat + 1) % IDLE_BEATS.len());
            return;
        }
        if self.state != state {
            self.state = state;
            self.direction = Direction::S;
            self.frame = 0;
            self.tick_counter = 0;
            self.hold_remaining = 0;
            self.hold_loop_from = None;
            // Begin each pacing bout from centre, walking west (art's native facing).
            if state == AnimState::Thinking {
                self.pace_x = 0.0;
                self.pace_sign = -1.0;
            }
        }
    }

    /// Advance one timer tick. Steps the frame animation and, while pacing
    /// (Thinking), also moves HK-47 across the floor. Returns true if anything
    /// changed and the sprite needs redrawing.
    pub fn tick(&mut self) -> bool {
        let mut changed = self.advance_frame();
        if self.state == AnimState::Thinking {
            self.advance_pace();
            changed = true; // position shifts every tick → always redraw
        }
        if self.is_idle() {
            self.idle_dwell = self.idle_dwell.saturating_sub(1);
            if self.idle_dwell == 0 {
                self.enter_idle_beat((self.idle_beat + 1) % IDLE_BEATS.len());
                changed = true;
            }
        }
        if self.beam().is_some() {
            changed = true; // the beam sweeps every tick, even with the pose frozen
        }
        changed
    }

    /// Step the frame index within the current state's range, honouring holds,
    /// mini-loops, and loop_start wrap. Returns true if the frame changed.
    fn advance_frame(&mut self) -> bool {
        let idle = self.is_idle();
        if self.hold_remaining > 0 {
            self.hold_remaining -= 1;
            // Mini-loop: animate within [loop_from..=hold_loop_end] during the hold.
            if let Some(loop_from) = self.hold_loop_from {
                let anim = self.theme.state_anim_dir(self.state, self.direction);
                self.tick_counter += 1;
                if self.tick_counter.is_multiple_of(anim.tick_divisor) {
                    let next = self.frame + 1;
                    self.frame = if next > self.hold_loop_end { loop_from } else { next };
                    return true;
                }
            }
            return false;
        }
        let anim = self.theme.state_anim_dir(self.state, self.direction);
        let divisor = anim.tick_divisor;
        if divisor == 0 {
            return false; // frozen
        }
        self.tick_counter += 1;
        if !self.tick_counter.is_multiple_of(divisor) {
            return false;
        }
        let frame_count = anim.sheet.frame_count();
        let next = self.frame + 1;
        if next >= frame_count && idle {
            // Settle into the pose, then hold it still for the rest of the beat.
            // Looping here is what made him fidget once a second.
            return false;
        }
        self.frame = if next >= frame_count { anim.loop_start } else { next };
        if let Some(hold) = anim.holds.get(&self.frame) {
            self.hold_remaining = hold.ticks;
            self.hold_loop_from = hold.loop_from;
            self.hold_loop_end = self.frame;
        }
        true
    }

    /// Move the pacing offset one tick and about-face at the rug edges.
    fn advance_pace(&mut self) {
        self.pace_x += PACE_PER_TICK * self.pace_sign;
        if self.pace_x >= PACE_HALF_RANGE {
            self.pace_x = PACE_HALF_RANGE;
            self.pace_sign = -1.0; // hit right edge → turn west
        } else if self.pace_x <= -PACE_HALF_RANGE {
            self.pace_x = -PACE_HALF_RANGE;
            self.pace_sign = 1.0; // hit left edge → turn east
        }
    }

    /// Pacing offset for the draw loop, or None when not pacing.
    /// Returns (horizontal offset from centre in native backdrop px, flip_h).
    /// flip_h is true when walking east — the walk art faces west by default.
    pub fn pace(&self) -> Option<(f64, bool)> {
        if self.state == AnimState::Thinking {
            Some((self.pace_x, self.pace_sign > 0.0))
        } else {
            None
        }
    }

    pub fn current_frame(&self) -> usize {
        self.frame
    }
}

/// Draw the current sprite frame onto a Cairo context.
///
/// Blits a single frame from the sprite sheet, scaled to `display_size` with
/// nearest-neighbor filtering for crisp pixel art.
pub fn draw_frame(
    cr: &cairo::Context,
    sheet: &SpriteSheet,
    frame_index: usize,
    display_size: f64,
) {
    let fw = sheet.frame_width() as f64;
    let fh = sheet.frame_height() as f64;
    let scale_x = display_size / fw;
    let scale_y = display_size / fh;

    // Pixel offset into the sprite sheet for this frame
    let src_x = frame_index as f64 * fw;

    cr.save().unwrap();
    // Scale from sprite pixels → display pixels
    cr.scale(scale_x, scale_y);
    // Position the sheet so the target frame sits at origin
    cr.set_source_surface(&sheet.surface, -src_x, 0.0).unwrap();
    // Nearest-neighbor: crisp pixel art, no blurry interpolation
    cr.source().set_filter(cairo::Filter::Nearest);
    // Clip to exactly one frame so adjacent frames don't bleed
    cr.rectangle(0.0, 0.0, fw, fh);
    cr.clip();
    cr.paint().unwrap();
    cr.restore().unwrap();
}

/// Ellipse width as a multiple of the footprint's own width.
const SHADOW_SPREAD: f64 = 1.15;
/// Ellipse height as a multiple of its width — how flat the floor reads.
const SHADOW_SQUASH: f64 = 0.16;

/// Draw the contact shadow under one sprite frame.
///
/// This lives in the draw loop rather than being baked into the backdrop for two
/// reasons: he translates across the floor while pacing in Thinking, and his
/// footprint changes with the pose. Both would strand a painted shadow.
///
/// `cx` and `floor_y` are in drawing-area coordinates, `scale` maps frame pixels
/// to screen pixels.
///
/// Drawn as three hard-edged bands with antialiasing off, not as a radial
/// gradient. A gradient is the obvious implementation and it is wrong here: at
/// 1:1 pixel density a smoothly-falling smudge reads as an airbrush laid over
/// pixel art, which is the exact seam the diorama exists to close. Discrete
/// bands on the pixel grid read as something drawn by the same hand as the room.
/// The bands go into a group with OPERATOR_SOURCE so the inner ones replace the
/// outer instead of compounding their alpha.
pub fn draw_shadow(cr: &cairo::Context, sheet: &SpriteSheet, frame_index: usize, cx: f64, floor_y: f64, scale: f64) {
    /// (radius as a fraction of rx, alpha) — outermost first.
    const BANDS: [(f64, f64); 3] = [(1.0, 0.18), (0.70, 0.38), (0.40, 0.58)];

    let (_, width) = sheet.metrics(frame_index).footprint;
    if width <= 0.0 {
        return;
    }
    let rx = width * scale * SHADOW_SPREAD / 2.0;
    let ry = (rx * SHADOW_SQUASH).max(1.5);

    cr.save().unwrap();
    // Snap to whole pixels and sit one sprite-pixel proud of the floor line, so
    // the ellipse's upper half tucks behind the soles rather than under them.
    cr.translate(cx.round(), (floor_y - scale).round());
    cr.scale(1.0, ry / rx);
    cr.set_antialias(cairo::Antialias::None);
    cr.push_group();
    for (frac, alpha) in BANDS {
        cr.set_operator(cairo::Operator::Source);
        cr.set_source_rgba(0.03, 0.02, 0.04, alpha);
        cr.arc(0.0, 0.0, rx * frac, 0.0, std::f64::consts::TAU);
        cr.fill().unwrap();
    }
    let group = cr.pop_group().unwrap();
    cr.set_operator(cairo::Operator::Over);
    cr.set_source(&group).unwrap();
    cr.paint().unwrap();
    cr.restore().unwrap();
}

/// Half-width of the beam wedge where it meets the far wall, in screen px.
const BEAM_HALF_WIDTH: f64 = 7.0;

/// Draw the scan beam: a red wedge from his photoreceptors to a point on the
/// interior's far wall, clipped to the room.
///
/// `interior` is (x, y, w, h) of the diorama's interior in drawing-area
/// coordinates — the beam must not spill onto the frame ring, which is a
/// physical object in front of the scene rather than part of it.
///
/// Drawn before the sprite so his own body occludes the near end, which is what
/// makes it read as light in a room instead of a decal over the top of him.
pub fn draw_beam(cr: &cairo::Context, beam: &Beam, eye_x: f64, eye_y: f64, interior: (f64, f64, f64, f64)) {
    let (ix, iy, iw, ih) = interior;
    // Target: a point on the near-side wall, swept top to bottom. Overshoot past
    // the wall so the wedge always exits the clip rather than ending in mid-air.
    let wall_x = if beam.side < 0.0 { ix } else { ix + iw };
    let target_y = iy + 4.0 + beam.t * (ih - 8.0);
    let dx = (wall_x - eye_x) * 1.3;
    let dy = (target_y - eye_y) * 1.3;
    let len = (dx * dx + dy * dy).sqrt();
    if len < 1.0 {
        return;
    }
    // Perpendicular to the axis, scaled to the wedge's half-width at the far end.
    let (px, py) = (-dy / len * BEAM_HALF_WIDTH, dx / len * BEAM_HALF_WIDTH);

    cr.save().unwrap();
    cr.set_antialias(cairo::Antialias::None);
    cr.rectangle(ix, iy, iw, ih);
    cr.clip();

    // The flash overrides the ramp for its few ticks, so the beam snaps to full
    // brightness at ignition and again at shutdown instead of fading politely.
    let lit = (beam.intensity + beam.flash).min(1.0);

    let grad = cairo::LinearGradient::new(eye_x, eye_y, eye_x + dx, eye_y + dy);
    grad.add_color_stop_rgba(0.0, 0.95, 0.20, 0.16, 0.42 * lit);
    grad.add_color_stop_rgba(1.0, 0.80, 0.10, 0.10, 0.0);
    cr.set_source(&grad).unwrap();
    cr.move_to(eye_x, eye_y);
    cr.line_to(eye_x + dx + px, eye_y + dy + py);
    cr.line_to(eye_x + dx - px, eye_y + dy - py);
    cr.close_path();
    cr.fill().unwrap();

    // A brighter core along the axis, so the wedge has an edge to lead with.
    let core = cairo::LinearGradient::new(eye_x, eye_y, eye_x + dx, eye_y + dy);
    core.add_color_stop_rgba(0.0, 1.0, 0.55, 0.45, 0.75 * lit);
    core.add_color_stop_rgba(1.0, 1.0, 0.25, 0.20, 0.0);
    cr.set_source(&core).unwrap();
    cr.set_line_width(1.0);
    cr.move_to(eye_x, eye_y);
    cr.line_to(eye_x + dx, eye_y + dy);
    cr.stroke().unwrap();

    cr.restore().unwrap();
}

/// Radius of the emitter bloom at full flash, in screen px.
const BEAM_FLASH_RADIUS: f64 = 10.0;

/// Draw the ignition / shutdown bloom at the photoreceptors.
///
/// Separate from `draw_beam` only because of z-order: the beam goes under the
/// sprite so his body occludes its near end, but the bloom is his own head
/// lighting up and has to sit over it.
pub fn draw_beam_flash(cr: &cairo::Context, beam: &Beam, eye_x: f64, eye_y: f64, interior: (f64, f64, f64, f64)) {
    if beam.flash <= 0.0 {
        return;
    }
    let (ix, iy, iw, ih) = interior;
    let r = (BEAM_FLASH_RADIUS * beam.flash).max(1.0);

    cr.save().unwrap();
    cr.set_antialias(cairo::Antialias::None);
    cr.rectangle(ix, iy, iw, ih);
    cr.clip();

    let glow = cairo::RadialGradient::new(eye_x, eye_y, 0.0, eye_x, eye_y, r);
    glow.add_color_stop_rgba(0.0, 1.0, 0.72, 0.62, 0.95 * beam.flash);
    glow.add_color_stop_rgba(0.45, 1.0, 0.26, 0.18, 0.60 * beam.flash);
    glow.add_color_stop_rgba(1.0, 0.90, 0.10, 0.08, 0.0);
    cr.set_source(&glow).unwrap();
    cr.arc(eye_x, eye_y, r, 0.0, std::f64::consts::TAU);
    cr.fill().unwrap();

    cr.restore().unwrap();
}

/// Draw a static single-frame image (the diorama backdrop) scaled uniformly by
/// `scale` from the origin, with nearest-neighbor filtering. Unlike `draw_frame`,
/// this preserves aspect ratio (no per-axis squash) and paints the whole image —
/// it's the room layer the animated sprite is later composited on top of.
pub fn draw_backdrop(cr: &cairo::Context, sheet: &SpriteSheet, scale: f64) {
    cr.save().unwrap();
    cr.scale(scale, scale);
    cr.set_source_surface(&sheet.surface, 0.0, 0.0).unwrap();
    cr.source().set_filter(cairo::Filter::Nearest);
    cr.paint().unwrap();
    cr.restore().unwrap();
}
