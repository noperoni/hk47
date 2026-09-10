//! Attention badge: how many Claude Code sessions currently want Master's attention.
//!
//! HK-47 cannot see other Claude Code sessions from inside his own process, so a
//! hook script (`contrib/hk47-badge-hook.py`) drops one flag file per session
//! under the runtime dir and this module counts them.
//!
//! Polling that directory beats a second IPC protocol on every axis that matters
//! here: the hook needs to know nothing about HK-47, the companion may be started
//! long after the sessions it is counting, and a reboot clears the state for free
//! because the runtime dir is tmpfs. It also matches the input-region poll in
//! main.rs, which is already the codebase's answer to "state that must self-heal".

use gtk4::cairo;
use std::path::PathBuf;

/// Flag-file suffix for a session stopped at a permission prompt or otherwise waiting.
const BLOCKED_EXT: &str = "blocked";
/// Flag-file suffix for a session carrying a tool failure that has not been answered.
const ERROR_EXT: &str = "error";

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Badge {
    /// Sessions stopped at a permission prompt, or notifying that they want input.
    pub blocked: usize,
    /// Sessions carrying an unacknowledged tool failure.
    pub errors: usize,
}

impl Badge {
    /// Total items pending. A session that is both blocked and errored counts
    /// twice on purpose: they are two separate things asking to be dealt with.
    pub fn total(self) -> usize {
        self.blocked + self.errors
    }

    pub fn is_empty(self) -> bool {
        self.total() == 0
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
/// pending — that is the normal state before any hook has ever fired, so it
/// reports zero rather than warning on every poll.
pub fn read() -> Badge {
    let mut badge = Badge::default();
    let Ok(entries) = std::fs::read_dir(badge_dir()) else {
        return badge;
    };
    for entry in entries.flatten() {
        match entry.path().extension().and_then(|e| e.to_str()) {
            Some(BLOCKED_EXT) => badge.blocked += 1,
            Some(ERROR_EXT) => badge.errors += 1,
            _ => {}
        }
    }
    badge
}

/// Draw the badge as a filled disc with the count in it, anchored to the
/// top-right corner of an area `area_w` wide. `size` is the disc diameter.
///
/// Amber while things are merely waiting, red once anything has actually failed,
/// on the assumption that a failure outranks a queue. Counts above 99 clamp to
/// "99+" so the glyph never outgrows the disc.
pub fn draw(cr: &cairo::Context, badge: Badge, area_w: f64, size: f64) {
    if badge.is_empty() {
        return;
    }

    let radius = size / 2.0;
    // Inset by a quarter-diameter from the top-right so the disc clears the frame edge.
    let cx = area_w - radius - size * 0.25;
    let cy = radius + size * 0.25;

    let (r, g, b) = if badge.errors > 0 {
        (0.75, 0.22, 0.17) // red: something failed
    } else {
        (0.84, 0.53, 0.06) // amber: something waits
    };

    cr.save().unwrap();

    cr.arc(cx, cy, radius, 0.0, std::f64::consts::TAU);
    cr.set_source_rgb(r, g, b);
    cr.fill_preserve().unwrap();
    // Dark rim so the disc stays legible against a light patch of backdrop.
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.65);
    cr.set_line_width(size * 0.08);
    cr.stroke().unwrap();

    let label = match badge.total() {
        n if n > 99 => "99+".to_string(),
        n => n.to_string(),
    };
    cr.select_font_face("sans-serif", cairo::FontSlant::Normal, cairo::FontWeight::Bold);
    cr.set_font_size(size * 0.62);
    cr.set_source_rgb(1.0, 1.0, 1.0);
    if let Ok(ext) = cr.text_extents(&label) {
        // Centre on the glyph's own ink box rather than on the font metrics,
        // so "1" and "99+" both sit centred instead of only the average case.
        cr.move_to(
            cx - ext.width() / 2.0 - ext.x_bearing(),
            cy - ext.height() / 2.0 - ext.y_bearing(),
        );
        let _ = cr.show_text(&label);
    }

    cr.restore().unwrap();
}
