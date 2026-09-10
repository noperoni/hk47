use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Config {
    #[serde(default)]
    pub sprite: SpriteConfig,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SpriteConfig {
    /// Animation frames per second
    #[serde(default = "default_fps")]
    pub fps: u32,
    /// Sprite display size in pixels. Divided by the theme's `[geometry]
    /// frame_size` this gives the diorama's scale, and the theme's
    /// `[geometry] scale` multiplies the figure back up from there — so the
    /// three numbers interlock and moving one means moving the others.
    #[serde(default = "default_sprite_size")]
    pub size: u32,
    /// Theme name — loads from ~/.config/hk47/sprites/{theme}/
    /// "default" = use embedded default theme.
    #[serde(default = "default_theme")]
    pub theme: String,
}

impl Default for SpriteConfig {
    fn default() -> Self {
        Self {
            fps: default_fps(),
            size: default_sprite_size(),
            theme: default_theme(),
        }
    }
}

fn default_fps() -> u32 {
    24
}
fn default_sprite_size() -> u32 {
    128
}
fn default_theme() -> String {
    "default".to_string()
}

/// Returns the config directory path: ~/.config/hk47/
pub fn config_dir() -> PathBuf {
    dirs_fallback().join("hk47")
}

/// Returns the config file path: ~/.config/hk47/config.toml
pub fn config_path() -> PathBuf {
    config_dir().join("config.toml")
}

/// Load config from disk, falling back to defaults for missing keys.
///
/// Unknown keys are ignored rather than rejected, which is what lets a config
/// written before the chat was removed keep working with its dead sections
/// still in it.
pub fn load() -> Config {
    let path = config_path();
    match fs::read_to_string(&path) {
        Ok(contents) => match toml::from_str::<Config>(&contents) {
            Ok(config) => config,
            Err(e) => {
                eprintln!("warn: invalid config at {}: {e}, using defaults", path.display());
                Config::default()
            }
        },
        Err(_) => Config::default(),
    }
}

/// Write default config to disk if it doesn't exist yet.
pub fn write_defaults_if_missing() {
    let path = config_path();
    if path.exists() {
        return;
    }
    let dir = config_dir();
    if let Err(e) = fs::create_dir_all(&dir) {
        eprintln!("warn: cannot create config dir {}: {e}", dir.display());
        return;
    }
    if let Err(e) = fs::write(&path, DEFAULT_CONFIG_TEMPLATE) {
        eprintln!("warn: cannot write default config to {}: {e}", path.display());
    }
}

/// Placement is not in here on purpose: HK-47 is an xdg-toplevel, and a Wayland
/// client cannot set its own coordinates. Where he opens is a Hyprland window
/// rule against `com.hk47.desktop` — see contrib/hyprland.conf.
const DEFAULT_CONFIG_TEMPLATE: &str = r#"[sprite]
fps = 24
# size / theme [geometry] frame_size = the diorama's scale; the theme's
# [geometry] scale multiplies the figure back up from there.
size = 128
theme = "default"
"#;

/// XDG config home fallback: $XDG_CONFIG_HOME or ~/.config
fn dirs_fallback() -> PathBuf {
    std::env::var("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
            PathBuf::from(home).join(".config")
        })
}
