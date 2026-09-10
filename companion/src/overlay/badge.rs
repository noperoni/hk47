//! Attention readouts: how many other Claude Code sessions are waiting on Master.
//!
//! HK-47 cannot see other Claude Code sessions from inside his own process, so a
//! hook script (`contrib/hk47-badge-hook.py`) drops one flag file per session
//! under the runtime dir and this module counts them.
//!
//! Polling that directory beats a second IPC protocol on every axis that matters
//! here: the hook needs to know nothing about HK-47, the companion may be started
//! long after the sessions it is counting, and a reboot clears the state for free
//! because the runtime dir is tmpfs.
//!
//! Three counts, one per way a session can be waiting, and errors deliberately
//! not among them: a failed tool call is the session's own business to report,
//! whereas this answers "who is waiting for me".
//!
//! # Why there is no badge
//!
//! This was a red disc with a sans-serif number bolted to the diorama's top-right
//! corner, and it read as a notification bubble that had wandered in from a phone.
//! Four replacement badges were drawn (riveted tags, a console housing, a
//! horizontal strip, a hull stencil), and all four were the same mistake in
//! better clothes: a UI element parked on top of a picture.
//!
//! The corridor is already full of bezelled screens. So a live counter now
//! repaints the glass inside one of them, and a dead counter leaves the backdrop
//! exactly as painted. At rest there is no evidence anything was ever added.
//!
//! Position is the identity: that panel always means that counter. That is what
//! makes the icons unnecessary, which matters, because no padlock survives being
//! drawn at seven pixels square.

use gtk4::cairo;
use std::path::PathBuf;

use super::theme::{Readout, Theme};

/// Flag-file suffixes. Kept in sync by hand with `contrib/hk47-badge-hook.py`.
const QUESTION: &str = "question";
const PERMISSION: &str = "permission";
const WAITING: &str = "waiting";

/// Theme keys for the three wall consoles, in the same order as `Badge::counts`.
const KEYS: [&str; 3] = [QUESTION, PERMISSION, WAITING];

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Badge {
    /// Sessions blocked on an AskUserQuestion tool call.
    pub question: usize,
    /// Sessions stopped at a permission prompt.
    pub permission: usize,
    /// Sessions that finished their turn and want a prompt.
    pub waiting: usize,
}

impl Badge {
    /// The three counts in theme-key order.
    fn counts(self) -> [usize; 3] {
        [self.question, self.permission, self.waiting]
    }

    pub fn is_empty(self) -> bool {
        self.counts().iter().all(|&n| n == 0)
    }
}

/// Flag directory: `$XDG_RUNTIME_DIR/hk47/badge`, falling back to `/run/user/$UID`.
/// Kept in sync by hand with the same path in `contrib/hk47-badge-hook.py`.
pub fn badge_dir() -> PathBuf {
    std::env::var("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            let uid = unsafe { libc::getuid() };
            PathBuf::from(format!("/run/user/{uid}"))
        })
        .join("hk47")
        .join("badge")
}

/// Count the flag files. A missing or unreadable directory means nothing is
/// pending: that is the normal state before any hook has ever fired, so it
/// reports zero rather than warning on every poll.
///
/// The hook keeps the three states mutually exclusive per session, so the counts
/// sum to the number of sessions waiting rather than to something larger.
pub fn read() -> Badge {
    let mut badge = Badge::default();
    let Ok(entries) = std::fs::read_dir(badge_dir()) else {
        return badge;
    };
    for entry in entries.flatten() {
        match entry.path().extension().and_then(|e| e.to_str()) {
            Some(QUESTION) => badge.question += 1,
            Some(PERMISSION) => badge.permission += 1,
            Some(WAITING) => badge.waiting += 1,
            _ => {}
        }
    }
    badge
}

/// Hand-built 3x5 numerals, one `u8` bitmask per row, bit 2 leftmost. Index 10
/// is the overflow `+`. A font this size has to be drawn rather than chosen: at
/// three pixels wide, hinting a real typeface produces mush.
const DIGITS: [[u8; 5]; 11] = [
    [0b111, 0b101, 0b101, 0b101, 0b111], // 0
    [0b010, 0b110, 0b010, 0b010, 0b111], // 1
    [0b111, 0b001, 0b111, 0b100, 0b111], // 2
    [0b111, 0b001, 0b111, 0b001, 0b111], // 3
    [0b101, 0b101, 0b111, 0b001, 0b001], // 4
    [0b111, 0b100, 0b111, 0b001, 0b111], // 5
    [0b111, 0b100, 0b111, 0b101, 0b111], // 6
    [0b111, 0b001, 0b001, 0b001, 0b001], // 7
    [0b111, 0b101, 0b111, 0b101, 0b111], // 8
    [0b111, 0b101, 0b111, 0b001, 0b111], // 9
    [0b000, 0b010, 0b111, 0b010, 0b000], // +
];

/// Glyph width in art pixels for `len` characters at `scale`, the trailing
/// inter-character gap excluded.
fn glyph_width(len: usize, scale: f64) -> f64 {
    (len as f64 * 4.0 - 1.0) * scale
}

/// Clearance kept between the numeral and the rim, per side, in art pixels.
const INSET: f64 = 2.0;

fn mix(a: [f64; 3], b: [f64; 3], t: f64) -> [f64; 3] {
    [
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    ]
}

fn set_rgb(cr: &cairo::Context, c: [f64; 3]) {
    cr.set_source_rgb(c[0], c[1], c[2]);
}

/// Draw the live counters onto the corridor's own wall consoles.
///
/// Call this in backdrop art-pixel space, between `draw_backdrop` and the
/// figure: the readouts are painted on the wall, so his body must occlude them
/// rather than the other way round. `scale` is the diorama scale, and every
/// rect is filled with antialiasing off so the readouts land on exactly the
/// same pixel grid the nearest-neighbour backdrop does.
pub fn draw(cr: &cairo::Context, badge: Badge, theme: &Theme, scale: f64) {
    if badge.is_empty() {
        return;
    }
    cr.save().unwrap();
    cr.scale(scale, scale);
    cr.set_antialias(cairo::Antialias::None);
    for (key, count) in KEYS.iter().zip(badge.counts()) {
        if count == 0 {
            continue;
        }
        if let Some(readout) = theme.readout(key) {
            light(cr, readout, count);
        }
    }
    cr.restore().unwrap();
}

/// Repaint one screen face as a lit readout showing `count`.
fn light(cr: &cairo::Context, readout: Readout, count: usize) {
    let [x0, y0, x1, y1] = readout.rect;
    let (w, h) = (x1 - x0, y1 - y0);
    if w < 5.0 || h < 7.0 {
        return; // too small to hold even the smallest numeral legibly
    }
    let tint = readout.tint();
    let black = [0.0, 0.0, 0.0];
    let glass = mix(black, tint, 0.12);

    // Dark glass, then every other row darker again. The scanline is what stops
    // a flat colour patch reading as a hole punched in the wall.
    set_rgb(cr, glass);
    cr.rectangle(x0, y0, w, h);
    cr.fill().unwrap();
    set_rgb(cr, mix(glass, black, 0.35));
    let mut y = y0 + 1.0;
    while y < y1 {
        cr.rectangle(x0, y, w, 1.0);
        y += 2.0;
    }
    cr.fill().unwrap();

    // Lit rim: the screen's own bezel glow, one pixel all the way round.
    set_rgb(cr, mix(black, tint, 0.42));
    cr.rectangle(x0, y0, w, 1.0);
    cr.rectangle(x0, y1 - 1.0, w, 1.0);
    cr.rectangle(x0, y0, 1.0, h);
    cr.rectangle(x1 - 1.0, y0, 1.0, h);
    cr.fill().unwrap();

    // Counts above nine clamp to "9+" so the numeral never outgrows the glass.
    let chars: &[usize] = if count > 9 { &[9, 10] } else { &[count] };
    // Chunky where it fits, small where it does not. The tall panels take the
    // double-size numeral for one digit and drop to single size for "9+",
    // which is why a flood reads quieter than a lone question does.
    let scale = if glyph_width(chars.len(), 2.0) + INSET * 2.0 <= w && 10.0 + INSET * 2.0 <= h {
        2.0
    } else {
        1.0
    };
    let gw = glyph_width(chars.len(), scale);
    let gh = 5.0 * scale;
    let gx = x0 + ((w - gw) / 2.0).floor();
    let gy = y0 + ((h - gh) / 2.0).floor();

    // Clipped to the glass inside the rim, so a bloom on a tight panel spills
    // into the screen rather than over the bezel.
    cr.save().unwrap();
    cr.rectangle(x0 + 1.0, y0 + 1.0, w - 2.0, h - 2.0);
    cr.clip();

    // Bloom first, as four copies of the numeral offset one pixel out on each
    // side; the bright pass then covers its own centre. Cheaper than testing
    // every neighbour, and identical in result once the numeral lands on top.
    set_rgb(cr, mix(glass, tint, 0.45));
    for (dx, dy) in [(1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)] {
        path_number(cr, chars, gx + dx, gy + dy, scale);
    }
    cr.fill().unwrap();
    set_rgb(cr, tint);
    path_number(cr, chars, gx, gy, scale);
    cr.fill().unwrap();

    cr.restore().unwrap();
}

/// Append one number's lit pixels to the current path as unit rectangles.
fn path_number(cr: &cairo::Context, chars: &[usize], x: f64, y: f64, scale: f64) {
    for (i, &ch) in chars.iter().enumerate() {
        let rows = &DIGITS[ch];
        let ox = x + i as f64 * 4.0 * scale;
        for (ry, &bits) in rows.iter().enumerate() {
            for rx in 0..3 {
                if bits & (0b100 >> rx) != 0 {
                    cr.rectangle(ox + rx as f64 * scale, y + ry as f64 * scale, scale, scale);
                }
            }
        }
    }
}
