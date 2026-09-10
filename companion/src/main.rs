mod config;
mod input;
mod overlay;

use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::Duration;

use gtk4::glib;
use gtk4::prelude::*;

fn main() {
    // CLI subcommands: forward the verb over IPC to a running instance and exit.
    // `quit` exits non-zero when nobody is listening, which is what lets the
    // SUPER+H script fall through to launching him: `hk47 quit || uwsm-app -- hk47`.
    match std::env::args().nth(1).as_deref() {
        Some(cmd @ ("toggle" | "quit")) => {
            input::socket::send_command(cmd);
            return;
        }
        Some(other) => {
            eprintln!("error: unknown command {other:?}");
            eprintln!("usage: hk47 [toggle|quit]");
            std::process::exit(2);
        }
        None => {}
    }

    // Load or generate config
    config::write_defaults_if_missing();
    let config = config::load();

    // IPC: spawn socket listener in a background Tokio thread
    let (toggle_tx, toggle_rx) = async_channel::bounded::<()>(8);

    std::thread::spawn(move || {
        let rt = tokio::runtime::Runtime::new().expect("failed to create tokio runtime");
        rt.block_on(async {
            let socket = tokio::spawn(async move {
                if let Err(e) = input::socket::listen(toggle_tx).await {
                    eprintln!("error: socket listener failed: {e}");
                }
            });
            let signal = tokio::spawn(async {
                input::socket::wait_for_signal().await;
            });
            let _ = tokio::join!(socket, signal);
        });
    });

    let app = gtk4::Application::builder()
        .application_id("com.hk47.desktop")
        .build();

    // Clean up socket on shutdown
    app.connect_shutdown(|_| {
        input::socket::cleanup();
    });

    let cfg = config.clone();
    app.connect_activate(move |app| {
        let window = overlay::window::build(app);

        // Create drawing area for sprite (size set below, once theme/backdrop is known)
        let drawing_area = gtk4::DrawingArea::new();

        // Load sprite theme and create shared animator
        let theme = Rc::new(overlay::theme::load_theme(&cfg.sprite.theme));
        let animator = Rc::new(RefCell::new(overlay::sprite::Animator::new(theme.clone())));

        // Diorama geometry. backdrop scale = sprite_size / native_frame_w (controls window size).
        // HK-47 is rendered at geom.scale × sprite_size and re-anchored so his soles (geom.feet_y
        // within the frame) land on the diorama's floor line (geom.floor_y within the backdrop).
        // All four numbers come from the theme, so a repack can re-frame him without a recompile;
        // for the hk47 pack sprite.size 96 over a 128px frame gives a diorama scale of 0.75, and
        // geom.scale 4/3 multiplies the figure back to exactly 1.0, native pixel density.
        let geom = theme.geometry();
        let sprite_size = cfg.sprite.size as f64;
        let frame_w = animator.borrow().current_sheet().frame_width() as f64;
        let diorama_scale = sprite_size / frame_w;
        let hk47_display_size = sprite_size * geom.scale;
        let frame_scale = hk47_display_size / frame_w;
        let floor_y_screen = geom.floor_y * diorama_scale;
        let oy_screen = floor_y_screen - geom.feet_y * frame_scale;

        // Size the drawing area to the backdrop (×scale) when present, else to the sprite.
        // The window is sized to its content, so this is also the window's size.
        match theme.backdrop() {
            Some(bd) => {
                drawing_area.set_content_width((bd.frame_width() as f64 * diorama_scale) as i32);
                drawing_area.set_content_height((bd.frame_height() as f64 * diorama_scale) as i32);
            }
            None => {
                drawing_area.set_content_width(cfg.sprite.size as i32);
                drawing_area.set_content_height(cfg.sprite.size as i32);
            }
        }

        // Attention badge: how many other Claude Code sessions want Master. Fed by
        // flag files a hook drops in the runtime dir; see overlay::badge.
        let badge = Rc::new(Cell::new(overlay::badge::read()));
        // Disc diameter, tied to the sprite so it scales with the configured size.
        let badge_size = sprite_size * 0.22;

        // Set up frame drawing: backdrop layer (if any) first, then the sprite on top,
        // then the badge above both so it is never hidden behind the diorama frame.
        let anim_draw = animator.clone();
        let theme_draw = theme.clone();
        let badge_draw = badge.clone();
        drawing_area.set_draw_func(move |_area, cr, w, _h| {
            let anim = anim_draw.borrow();
            if let Some(bd) = theme_draw.backdrop() {
                overlay::sprite::draw_backdrop(cr, bd, diorama_scale);
                // Base position: horizontally centred on the floor.
                let mut ox_screen = (bd.frame_width() as f64 * diorama_scale - hk47_display_size) / 2.0;
                // While pacing (Thinking), shift along the floor and mirror the
                // west-facing walk art when he's heading east.
                let flip_h = match anim.pace() {
                    Some((pace_native, flip)) => {
                        ox_screen += pace_native * diorama_scale;
                        flip
                    }
                    None => false,
                };
                // Contact shadow first, so his soles overlap its near edge. Its
                // centre follows the footprint, which is off-frame-centre for the
                // wider poses and mirrors with him when he walks east.
                let sheet = anim.current_sheet();
                let metrics = sheet.metrics(anim.current_frame());
                // Mirror a frame-local x when the walk art is flipped.
                let mirror = |x: f64| if flip_h { frame_w - x } else { x };
                overlay::sprite::draw_shadow(
                    cr,
                    sheet,
                    anim.current_frame(),
                    ox_screen + mirror(metrics.footprint.0) * frame_scale,
                    floor_y_screen,
                    frame_scale,
                );
                // Scan beam, under the sprite so his own body occludes the near
                // end of it. Clipped to the interior: the frame line is an object
                // in front of the scene, not part of the room being scanned.
                let beam = anim.beam();
                let eye_x = ox_screen + mirror(metrics.eye.0) * frame_scale;
                let eye_y = oy_screen + metrics.eye.1 * frame_scale;
                let ring = geom.border as f64 * diorama_scale;
                let interior = (
                    ring,
                    ring,
                    bd.frame_width() as f64 * diorama_scale - ring * 2.0,
                    bd.frame_height() as f64 * diorama_scale - ring * 2.0,
                );
                if let Some(beam) = &beam {
                    overlay::sprite::draw_beam(cr, beam, eye_x, eye_y, interior);
                }
                cr.save().unwrap();
                cr.translate(ox_screen, oy_screen);
                if flip_h {
                    // Mirror horizontally about the sprite box's own centre.
                    cr.translate(hk47_display_size, 0.0);
                    cr.scale(-1.0, 1.0);
                }
                overlay::sprite::draw_frame(cr, anim.current_sheet(), anim.current_frame(), hk47_display_size);
                cr.restore().unwrap();
                // Emitter bloom last: it is his own head lighting up, so unlike
                // the beam it belongs over the sprite rather than under it.
                if let Some(beam) = &beam {
                    overlay::sprite::draw_beam_flash(cr, beam, eye_x, eye_y, interior);
                }
            } else {
                overlay::sprite::draw_frame(cr, anim.current_sheet(), anim.current_frame(), sprite_size);
            }
            overlay::badge::draw(cr, badge_draw.get(), w as f64, badge_size);
        });

        // Animation timer
        let anim_tick = animator.clone();
        let da = drawing_area.clone();
        let frame_interval = Duration::from_millis(1000 / cfg.sprite.fps.max(1) as u64);
        glib::timeout_add_local(frame_interval, move || {
            if anim_tick.borrow_mut().tick() {
                da.queue_draw();
            }
            glib::ControlFlow::Continue
        });

        // Attention-badge poll. The flag files are written by hooks in other
        // processes, so there is no signal to wait on. 1 Hz sits far below the
        // rate at which a human answers a permission prompt and costs one
        // readdir of a tmpfs directory that is usually empty.
        //
        // The error count is also what drives AnimState::Error, on the edge only:
        // set_state(Idle) cuts to the next idle beat, so calling it every second
        // would restart the rotation forever and he would never hold a pose.
        let badge_poll = badge.clone();
        let anim_badge = animator.clone();
        let da_badge = drawing_area.clone();
        glib::timeout_add_local(Duration::from_secs(1), move || {
            let current = overlay::badge::read();
            let previous = badge_poll.get();
            if current != previous {
                badge_poll.set(current);
                if (current.errors > 0) != (previous.errors > 0) {
                    let state = if current.errors > 0 {
                        overlay::sprite::AnimState::Error
                    } else {
                        overlay::sprite::AnimState::Idle
                    };
                    anim_badge.borrow_mut().set_state(state);
                }
                da_badge.queue_draw();
            }
            glib::ControlFlow::Continue
        });

        // HK-47 is moved with SUPER+left-drag, handled entirely by the compositor
        // (Omarchy's `bindm = SUPER, mouse, movewindow`). Nothing in this process
        // handles clicks: he is a sprite in a diorama, not a button.

        // `hk47 toggle` hides and shows him without stopping the process, so his
        // idle rotation and badge state survive being got out of the way. Killing
        // him outright is `hk47 quit`, which is what SUPER+H is bound to.
        let toggle_win = window.clone();
        let rx = toggle_rx.clone();
        glib::spawn_future_local(async move {
            while rx.recv().await.is_ok() {
                if toggle_win.is_visible() {
                    toggle_win.set_visible(false);
                } else {
                    toggle_win.present();
                }
            }
        });

        window.set_child(Some(&drawing_area));
        window.present();

        // Keep the animator alive for the lifetime of the window
        let _animator = animator;
    });

    app.run();
}
