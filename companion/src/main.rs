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

    // IPC channel to the GTK thread. The listener behind it is NOT started here:
    // see connect_startup below.
    let (toggle_tx, toggle_rx) = async_channel::bounded::<()>(8);

    let app = gtk4::Application::builder()
        .application_id("com.hk47.desktop")
        .build();

    // The socket is bound by the primary instance and by nothing else.
    //
    // It used to be bound before GTK ran at all, which made a second `hk47` unlink
    // the live listener's socket and rebind it, then discover the primary over
    // D-Bus and exit, taking the socket file with it. The instance still on screen
    // was left unreachable: `hk47 quit` answered "connection refused", and the
    // SUPER+H script reads a failed quit as "he is not running", so it launched yet
    // another duplicate. `startup` fires exactly once and only on the process that
    // owns the app id, which is precisely the process that should own the socket.
    app.connect_startup(move |_| {
        let tx = toggle_tx.clone();
        std::thread::spawn(move || {
            let rt = tokio::runtime::Runtime::new().expect("failed to create tokio runtime");
            rt.block_on(async {
                let socket = tokio::spawn(async move {
                    if let Err(e) = input::socket::listen(tx).await {
                        eprintln!("error: socket listener failed: {e}");
                    }
                });
                let signal = tokio::spawn(async {
                    input::socket::wait_for_signal().await;
                });
                let _ = tokio::join!(socket, signal);
            });
        });
    });

    // Clean up socket on shutdown. Safe to register unconditionally: a remote
    // instance never emits startup or shutdown, so it can never delete the socket
    // that belongs to the primary.
    app.connect_shutdown(|_| {
        input::socket::cleanup();
    });

    let cfg = config.clone();
    app.connect_activate(move |app| {
        // A second `hk47` launch arrives here as another activate on the primary,
        // which used to build a whole second diorama in the same process: one pid,
        // two identical windows, one animator each. Present the one that exists
        // instead, which also brings him back if `hk47 toggle` had hidden him.
        if let Some(existing) = app.windows().first() {
            existing.present();
            return;
        }

        let window = overlay::window::build(app);

        // Create drawing area for sprite (size set below, once theme/backdrop is known)
        let drawing_area = gtk4::DrawingArea::new();

        // Load sprite theme and create shared animator
        let theme = Rc::new(overlay::theme::load_theme(&cfg.sprite.theme));
        let animator = Rc::new(RefCell::new(overlay::sprite::Animator::new(theme.clone())));

        // Diorama geometry. The theme owns the four numbers that relate the room to
        // the figure: HK-47 is rendered at geom.scale × the diorama scale and re-anchored
        // so his soles (geom.feet_y within the frame) land on the diorama's floor line
        // (geom.floor_y within the backdrop). All four come from the theme, so a repack
        // can re-frame him without a recompile.
        //
        // The diorama scale itself is NOT computed here: it is derived per draw from the
        // widget's own allocation, which is what lets the window be resized by hand and
        // join the tiling layout. See the draw function below.
        let geom = theme.geometry();
        let frame_w = animator.borrow().current_sheet().frame_width() as f64;

        // The diorama scale at which HK-47's own art lands at exactly 1.0, native pixel
        // density: for the hk47 pack, geom.scale 4/3 makes that 0.75. The pack is not
        // built at 1:1 throughout, the room being drawn at 0.75 and only the figure
        // native, so this rather than 1.0 is where his art stops losing detail.
        let native_scale = 1.0 / geom.scale;

        // Content size = the diorama at native density. This is a floor, not a fixed
        // size: the window grows freely from here, but below it his art would fall
        // under native density, which no amount of filtering gets back.
        let (min_w, min_h) = match theme.backdrop() {
            Some(bd) => (
                (bd.frame_width() as f64 * native_scale).round() as i32,
                (bd.frame_height() as f64 * native_scale).round() as i32,
            ),
            None => (cfg.sprite.size as i32, cfg.sprite.size as i32),
        };
        drawing_area.set_content_width(min_w);
        drawing_area.set_content_height(min_h);

        // Opening size comes from the configured sprite size, exactly as it did when the
        // window was fixed, so `sprite.size` still says how big he starts. It is only the
        // initial size now; the compositor and Master's mouse own it after that.
        match theme.backdrop() {
            Some(bd) => {
                let init = cfg.sprite.size as f64 / frame_w;
                window.set_default_size(
                    (bd.frame_width() as f64 * init).round() as i32,
                    (bd.frame_height() as f64 * init).round() as i32,
                );
            }
            None => window.set_default_size(cfg.sprite.size as i32, cfg.sprite.size as i32),
        }

        // Attention badge: how many other Claude Code sessions want Master. Fed by
        // flag files a hook drops in the runtime dir; see overlay::badge.
        let badge = Rc::new(Cell::new(overlay::badge::read()));

        // Set up frame drawing: backdrop layer (if any) first, then the sprite on top,
        // then the badge above both so it is never hidden behind the diorama frame.
        //
        // Every number here is derived from the allocation the compositor handed us
        // rather than from the configured size, which is the whole of what makes him
        // resizable and tileable. The scale snaps DOWN to a whole multiple of
        // `scale_step` and the slack is left transparent, because a diorama letterboxed
        // in its tile keeps every art pixel square, where stretching to fill the tile
        // exactly would give uneven pixel runs on art drawn at native density.
        let anim_draw = animator.clone();
        let theme_draw = theme.clone();
        let badge_draw = badge.clone();
        drawing_area.set_draw_func(move |_area, cr, w, h| {
            let anim = anim_draw.borrow();
            if let Some(bd) = theme_draw.backdrop() {
                let bw = bd.frame_width() as f64;
                let bh = bd.frame_height() as f64;

                // Largest scale that fits the box, taken continuously rather than snapped
                // to whole art pixels. Snapping was built first and rejected: it throws
                // away up to a whole step, and a tile a little too small for the next
                // step up strands the diorama in a wide transparent hole. Master wants
                // the picture itself sizing up, so the only gap left is the aspect
                // mismatch between his tile and the diorama's own 264:232.
                //
                // The cost is uneven pixel runs at non-integer scales, which is the price
                // of an arbitrary window size on art built at a fixed density. Aspect is
                // preserved: stretching to fill both axes exactly would close the last
                // sliver at the cost of squashing the room, which is a worse trade.
                let diorama_scale = (w as f64 / bw).min(h as f64 / bh);
                let frame_scale = diorama_scale * geom.scale;
                let hk47_display_size = frame_w * frame_scale;
                let floor_y_screen = geom.floor_y * diorama_scale;
                let oy_screen = floor_y_screen - geom.feet_y * frame_scale;

                // Centre the diorama in whatever box we were given, on whole pixels so
                // the snapped scale is not undone by a half-pixel translation.
                let off_x = ((w as f64 - bw * diorama_scale) / 2.0).floor();
                let off_y = ((h as f64 - bh * diorama_scale) / 2.0).floor();
                cr.save().unwrap();
                cr.translate(off_x, off_y);

                overlay::sprite::draw_backdrop(cr, bd, diorama_scale);
                // Attention counters, lit on the corridor's own wall consoles. They
                // are painted on the wall, so they are drawn with the room rather
                // than over it: his body occludes them, not the reverse. At zero
                // nothing is drawn at all and the backdrop stands as painted.
                overlay::badge::draw(cr, badge_draw.get(), &theme_draw, diorama_scale);
                // Base position: horizontally centred on the floor.
                let mut ox_screen = (bw * diorama_scale - hk47_display_size) / 2.0;
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
                    bw * diorama_scale - ring * 2.0,
                    bh * diorama_scale - ring * 2.0,
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
                cr.restore().unwrap();
            } else {
                // No backdrop means no corridor and so no consoles to light. A
                // theme without a diorama shows the figure and nothing else,
                // rather than growing a floating badge back for the occasion.
                let size = (w.min(h)) as f64;
                overlay::sprite::draw_frame(cr, anim.current_sheet(), anim.current_frame(), size);
            }
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
        // The badge drives the badge and nothing else. It used to put him into
        // an error state on the zero-to-non-zero edge, which parked him in a
        // nine-frame strip looping every 0.75s until someone typed into the
        // session that had failed: a background session's failure could hold
        // him there for hours. Unwired on 2026-09-10, then deleted outright on
        // 2026-09-11, art and enum variant and all, because an error passes too
        // fast to be worth a glance at a sprite nobody is watching.
        //
        // The idle rotation is now the whole behaviour, by decision and not by
        // omission: nothing outside this process interrupts it, whatever gets
        // pinged. Attentive and Thinking keep their art and their variants and
        // are driven by nothing, for the same reasons in miniature: one is for
        // a sprite you are typing at, and the other is true so constantly that
        // it says nothing. A fork that wants states can wire set_state.
        let badge_poll = badge.clone();
        let da_badge = drawing_area.clone();
        glib::timeout_add_local(Duration::from_secs(1), move || {
            let current = overlay::badge::read();
            if current != badge_poll.get() {
                badge_poll.set(current);
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
