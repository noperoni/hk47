/// Build the borderless, transparent top-level companion window.
///
/// HK-47 is a normal xdg-toplevel (not a layer-shell surface) so the compositor
/// can move it freely across every monitor. On Omarchy/Hyprland the window is dragged
/// with SUPER+left-drag (`bindm = SUPER, mouse, movewindow`), a pure compositor move —
/// true 1:1 across outputs, which layer-shell surfaces (bound to a single output)
/// cannot do.
///
/// Tradeoff: as a toplevel, HK-47 cannot render above a fullscreen window the way
/// a layer-shell overlay could. Acceptable for a desktop companion.
///
/// The window is sized to its content (`resizable = false`) and never positions
/// itself — Wayland clients cannot set their own coordinates, so initial placement
/// comes from a Hyprland window rule (see contrib/hyprland.conf).
pub fn build(app: &gtk4::Application) -> gtk4::ApplicationWindow {
    let window = gtk4::ApplicationWindow::builder()
        .application(app)
        .title("HK-47")
        .decorated(false) // borderless — no titlebar / server-side decorations
        .resizable(false) // window always matches content size
        .build();

    // Transparent background so only the diorama is visible. The window is
    // exactly the size of the drawing area, so there is no transparent slack
    // for clicks to fall through and no input region to manage.
    let css = gtk4::CssProvider::new();
    css.load_from_string("window, drawingarea { background: transparent; }");
    gtk4::style_context_add_provider_for_display(
        &gtk4::gdk::Display::default().expect("no display"),
        &css,
        gtk4::STYLE_PROVIDER_PRIORITY_APPLICATION,
    );

    window
}
