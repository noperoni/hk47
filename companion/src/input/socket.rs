use std::io::Write as _;
use std::path::PathBuf;
use tokio::io::AsyncReadExt as _;
use tokio::net::UnixListener;

/// Returns the socket path: /run/user/$UID/hk47.sock
pub fn socket_path() -> PathBuf {
    let uid = unsafe { libc::getuid() };
    PathBuf::from(format!("/run/user/{uid}/hk47.sock"))
}

/// Remove the socket file if it exists (for clean shutdown).
pub fn cleanup() {
    let path = socket_path();
    match std::fs::remove_file(&path) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => eprintln!("warn: failed to remove socket {}: {e}", path.display()),
    }
}

/// Listen for commands on the Unix domain socket.
///
/// The payload is a bare verb. `quit` shuts the process down from here, `ping` is a
/// liveness probe that does nothing, and anything else, including an empty payload,
/// is a toggle and is forwarded through the channel to the GTK thread, which hides
/// or shows the window. The empty case keeps older callers working, which signalled
/// a toggle by connecting and closing without writing anything.
///
/// Call this from the primary GTK instance only. See `connect_startup` in main.rs.
pub async fn listen(tx: async_channel::Sender<()>) -> std::io::Result<()> {
    let path = socket_path();

    // A socket file is either a live listener or a leftover from an unclean exit,
    // and connecting to it is the only way to tell those apart. Probe before
    // unlinking: silently stealing the path from a running instance is what left
    // one unreachable, so that `hk47 quit` answered "connection refused" and the
    // SUPER+H script read that as "not running" and launched another duplicate.
    //
    // The probe writes `ping` rather than connecting and closing, because an empty
    // payload is the legacy spelling of `toggle` and would hide his window as a
    // side effect of asking whether he is there.
    if let Ok(mut probe) = std::os::unix::net::UnixStream::connect(&path) {
        let _ = probe.write_all(b"ping");
        return Err(std::io::Error::new(
            std::io::ErrorKind::AddrInUse,
            format!("another hk47 already answers on {}", path.display()),
        ));
    }

    // Nothing answered, so whatever is there is stale. Remove it (atomic, no TOCTOU).
    match std::fs::remove_file(&path) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e),
    }

    let listener = UnixListener::bind(&path)?;
    eprintln!("info: listening on {}", path.display());
    // H, not Alfred's B: this fork is built to run alongside him (see README).
    // One key for presence, so the hint advertises the script rather than `toggle`;
    // SUPER CTRL H is Omarchy's hardware menu and must not be taken.
    eprintln!("hint: commands: hk47 toggle (hide/show), hk47 quit (stop)");
    eprintln!("hint: bind in Hyprland: SUPER, H → hk47 quit 2>/dev/null || uwsm-app -- hk47");

    loop {
        match listener.accept().await {
            Ok((mut stream, _addr)) => {
                // A verb is a handful of bytes written in one go, and the client
                // closes straight after, so one bounded read is the whole payload.
                let mut buf = [0u8; 32];
                let cmd = match stream.read(&mut buf).await {
                    Ok(n) => String::from_utf8_lossy(&buf[..n]).trim().to_string(),
                    Err(e) => {
                        eprintln!("warn: socket read error: {e}");
                        continue;
                    }
                };
                match cmd.as_str() {
                    "quit" => {
                        eprintln!("info: received quit, cleaning up");
                        cleanup();
                        std::process::exit(0);
                    }
                    "" | "toggle" => {
                        if tx.send(()).await.is_err() {
                            break; // receiver dropped
                        }
                    }
                    // Liveness probe from a second instance starting up. Answering
                    // by accepting the connection is the whole reply; there is
                    // deliberately nothing to do.
                    "ping" => {}
                    other => eprintln!("warn: unknown socket command: {other:?}"),
                }
            }
            Err(e) => {
                eprintln!("warn: socket accept error: {e}");
            }
        }
    }

    Ok(())
}

/// Send a command to a running hk47 instance, then close.
/// The close is what tells the listener the payload is complete.
pub fn send_command(cmd: &str) {
    let path = socket_path();
    match std::os::unix::net::UnixStream::connect(&path) {
        Ok(mut stream) => {
            if let Err(e) = stream.write_all(cmd.as_bytes()) {
                eprintln!("error: failed to send {cmd:?} to hk47: {e}");
                std::process::exit(1);
            }
        }
        Err(e) => {
            eprintln!("error: cannot reach hk47 at {}: {e}", path.display());
            eprintln!("hint: is hk47 running?");
            std::process::exit(1);
        }
    }
}

/// Wait for SIGTERM or SIGINT, then clean up the socket and exit.
pub async fn wait_for_signal() {
    use tokio::signal::unix::{signal, SignalKind};

    let mut sigterm = signal(SignalKind::terminate()).expect("failed to register SIGTERM handler");
    let mut sigint = signal(SignalKind::interrupt()).expect("failed to register SIGINT handler");

    tokio::select! {
        _ = sigterm.recv() => eprintln!("\ninfo: received SIGTERM, cleaning up"),
        _ = sigint.recv() => eprintln!("\ninfo: received SIGINT, cleaning up"),
    }

    cleanup();
    std::process::exit(0);
}
