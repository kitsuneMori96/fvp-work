//! fvp-preview: HCB 整场景预览窗（M2 A 轨）。
//!
//! 输入：stdin 行分隔 JSON 快照（见 [`Snapshot`]），编辑器光标每动一次推一行，
//! 窗口 1 帧内刷新。渲染数学全部走 `fvp-preview-core`（与 rfvp 同一份语义）。
//!
//! 快照示例：
//! ```json
//! {"viewport":{"w":800.0,"h":600.0},"camera":{"x":0,"y":0,"z":0},"prims":[
//!   {"id":186,"parent":null,"draw":true,"x":0,"y":0,"z":1000,
//!    "angle":0,"fx":1000,"fy":1000,"attr":0,"opx":0,"opy":0,
//!    "u":0,"v":0,"off_x":0.0,"off_y":0.0,"alpha":255,
//!    "image":"/mnt/d/soft/Sakura moyu/graph/BG001_020.png"}
//! ]}
//! ```
//!
//! 非目标（明确不做）：像素级复刻（NVSG tile/色表、抗锯齿、插值差异均不在验收内）。

use std::collections::HashMap;
use std::io::BufRead;
use std::sync::mpsc::{self, Receiver};

use eframe::egui;
use fvp_preview_core::affine::Pos2 as CorePos2;
use fvp_preview_core::model::{self, Camera, PrimParams, Viewport};

// ---------------------------------------------------------------------------
// 快照协议（A 轨 JSON）
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, serde::Deserialize)]
struct Snapshot {
    #[serde(default = "default_viewport")]
    viewport: ViewportSer,
    #[serde(default)]
    camera: CameraSer,
    #[serde(default)]
    prims: Vec<PrimSer>,
}

#[derive(Debug, Clone, Copy, serde::Deserialize)]
struct ViewportSer {
    w: f32,
    h: f32,
}

#[derive(Debug, Clone, Copy, Default, serde::Deserialize)]
struct CameraSer {
    #[serde(default)]
    x: i32,
    #[serde(default)]
    y: i32,
    #[serde(default)]
    z: i32,
}

#[derive(Debug, Clone, serde::Deserialize)]
struct PrimSer {
    id: i32,
    #[serde(default)]
    parent: Option<i32>,
    #[serde(default = "default_true")]
    draw: bool,
    #[serde(default)]
    x: i32,
    #[serde(default)]
    y: i32,
    #[serde(default)]
    z: i32,
    #[serde(default)]
    angle: i32,
    #[serde(default = "default_mille")]
    fx: i32,
    #[serde(default = "default_mille")]
    fy: i32,
    #[serde(default)]
    attr: i32,
    #[serde(default)]
    opx: i32,
    #[serde(default)]
    opy: i32,
    #[serde(default)]
    u: i32,
    #[serde(default)]
    v: i32,
    #[serde(default)]
    off_x: f32,
    #[serde(default)]
    off_y: f32,
    #[serde(default = "default_alpha")]
    alpha: u8,
    #[serde(default)]
    image: String,
}

fn default_viewport() -> ViewportSer {
    ViewportSer { w: 800.0, h: 600.0 }
}

fn default_true() -> bool {
    true
}

fn default_mille() -> i32 {
    1000
}

fn default_alpha() -> u8 {
    255
}

impl PrimSer {
    fn params(&self) -> PrimParams {
        PrimParams {
            x: self.x,
            y: self.y,
            z: self.z,
            angle: self.angle,
            factor_x: self.fx,
            factor_y: self.fy,
            attr: self.attr,
            opx: self.opx,
            opy: self.opy,
            graph_u: self.u,
            graph_v: self.v,
            off_x: self.off_x,
            off_y: self.off_y,
        }
    }
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

struct TexEntry {
    handle: egui::TextureHandle,
    w: f32,
    h: f32,
}

struct PreviewApp {
    rx: Receiver<Snapshot>,
    scene: Option<Snapshot>,
    textures: HashMap<String, TexEntry>,
    status: String,
}

impl PreviewApp {
    fn new(rx: Receiver<Snapshot>) -> Self {
        Self {
            rx,
            scene: None,
            textures: HashMap::new(),
            status: "等待快照（stdin JSON）…".to_string(),
        }
    }

    fn poll_stdin(&mut self, ctx: &egui::Context) {
        let mut updated = false;
        while let Ok(snap) = self.rx.try_recv() {
            self.scene = Some(snap);
            updated = true;
        }
        if updated {
            let n = self.scene.as_ref().map(|s| s.prims.len()).unwrap_or(0);
            self.status = format!("已加载快照：{} prim", n);
            ctx.request_repaint();
        }
    }

    fn texture_for(
        &mut self,
        ctx: &egui::Context,
        path: &str,
    ) -> Option<(egui::TextureId, f32, f32)> {
        if path.is_empty() {
            return None;
        }
        if !self.textures.contains_key(path) {
            let img = match image::open(path) {
                Ok(img) => img.to_rgba8(),
                Err(e) => {
                    self.status = format!("图片加载失败 {}: {}", path, e);
                    return None;
                }
            };
            let (w, h) = (img.width(), img.height());
            let color =
                egui::ColorImage::from_rgba_unmultiplied([w as usize, h as usize], img.as_raw());
            let handle = ctx.load_texture(path.to_owned(), color, egui::TextureOptions::LINEAR);
            self.textures.insert(
                path.to_owned(),
                TexEntry {
                    handle,
                    w: w as f32,
                    h: h as f32,
                },
            );
        }
        self.textures
            .get(path)
            .map(|t| (t.handle.id(), t.w, t.h))
    }

    /// 按 rfvp 树序（DFS，draw=false 连子树一起跳过）展开 prim，
    /// 父链 x/y 逐级累加（`renderer.rs:187`）。
    fn draw_order(scene: &Snapshot) -> Vec<(usize, f32, f32)> {
        let idx_of: HashMap<i32, usize> = scene
            .prims
            .iter()
            .enumerate()
            .map(|(i, p)| (p.id, i))
            .collect();
        let mut children: HashMap<Option<i32>, Vec<usize>> = HashMap::new();
        for (i, p) in scene.prims.iter().enumerate() {
            let key = match p.parent {
                Some(pid) if idx_of.contains_key(&pid) => Some(pid),
                _ => None, // 父不存在 -> 当根，避免丢 prim
            };
            children.entry(key).or_default().push(i);
        }
        let mut out = Vec::new();
        let mut stack: Vec<(usize, f32, f32, bool)> = children
            .get(&None)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(|i| (i, 0.0, 0.0, false))
            .collect();
        // 逆序压栈以保持数组原序
        stack.reverse();
        let mut guard = 0usize;
        while let Some((i, px, py, hidden)) = stack.pop() {
            guard += 1;
            if guard > 65536 {
                break; // 环保护
            }
            let p = &scene.prims[i];
            let hidden = hidden || !p.draw;
            if !hidden {
                out.push((i, px, py));
            }
            if let Some(kids) = children.get(&Some(p.id)) {
                for &k in kids.iter().rev() {
                    stack.push((k, px + p.x as f32, py + p.y as f32, hidden));
                }
            }
        }
        out
    }
}

impl eframe::App for PreviewApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.poll_stdin(ctx);

        egui::TopBottomPanel::top("status").show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.label("fvp-preview（A轨快照）");
                ui.separator();
                ui.label(&self.status);
            });
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            let Some(scene) = self.scene.clone() else {
                ui.centered_and_justified(|ui| {
                    ui.label("echo '<snapshot.json>' | fvp-preview");
                });
                return;
            };
            let cam = Camera {
                x: scene.camera.x,
                y: scene.camera.y,
                z: scene.camera.z,
            };
            let vp = Viewport {
                w: scene.viewport.w,
                h: scene.viewport.h,
            };
            let painter = ui.painter();
            let origin = ui.min_rect().min;
            for (i, px, py) in Self::draw_order(&scene) {
                let prim = &scene.prims[i];
                let Some((tex_id, w, h)) = self.texture_for(ctx, &prim.image) else {
                    continue;
                };
                let corners = model::quad_corners(&prim.params(), w, h, px, py, &cam, &vp);
                let tint = egui::Color32::from_rgba_unmultiplied(255, 255, 255, prim.alpha);
                let to_egui = |c: CorePos2| egui::Pos2::new(origin.x + c.x, origin.y + c.y);
                let mut mesh = egui::Mesh::default();
                mesh.texture_id = tex_id;
                for (k, c) in corners.iter().enumerate() {
                    let (u, v) = match k {
                        0 => (0.0, 0.0),
                        1 => (1.0, 0.0),
                        2 => (1.0, 1.0),
                        _ => (0.0, 1.0),
                    };
                    mesh.vertices.push(egui::epaint::Vertex {
                        pos: to_egui(*c),
                        uv: egui::Pos2::new(u, v),
                        color: tint,
                    });
                }
                mesh.indices.extend_from_slice(&[0, 1, 2, 0, 2, 3]);
                painter.add(egui::Shape::mesh(mesh));
            }
        });
        // 有新快照才重画就够了，但 stdin 线程随时可能来数据，保持低频轮询。
        ctx.request_repaint_after(std::time::Duration::from_millis(100));
    }
}

/// CJK 字体顺位：Noto -> 文泉驿 -> Windows 微软雅黑（WSL 可读 C 盘）。
/// 找不到则回退 eframe 默认字体（中文状态栏会显示方框，但不影响渲染）。
fn setup_cjk_font(ctx: &egui::Context) {
    const CANDIDATES: &[&str] = &[
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/mnt/c/Windows/Fonts/msyh.ttc",
    ];
    for path in CANDIDATES {
        if let Ok(data) = std::fs::read(path) {
            let mut fonts = egui::FontDefinitions::default();
            fonts.font_data.insert("cjk".to_owned(), egui::FontData::from_owned(data));
            for family in [egui::FontFamily::Proportional, egui::FontFamily::Monospace] {
                fonts.families.entry(family).or_default().insert(0, "cjk".to_owned());
            }
            ctx.set_fonts(fonts);
            eprintln!("[fvp-preview] CJK 字体: {}", path);
            return;
        }
    }
    eprintln!("[fvp-preview] 未找到 CJK 字体，中文将显示为方框");
}

fn main() -> eframe::Result<()> {
    let (tx, rx) = mpsc::channel::<Snapshot>();
    std::thread::spawn(move || {
        let stdin = std::io::stdin();
        for line in stdin.lock().lines() {
            let line = match line {
                Ok(l) => l,
                Err(_) => break,
            };
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            match serde_json::from_str::<Snapshot>(line) {
                Ok(snap) => {
                    if tx.send(snap).is_err() {
                        break;
                    }
                }
                Err(e) => eprintln!("[fvp-preview] 快照解析失败: {}", e),
            }
        }
    });

    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title("fvp-preview")
            .with_inner_size([900.0, 700.0]),
        ..Default::default()
    };
    eframe::run_native(
        "fvp-preview",
        options,
        Box::new(|cc| {
            setup_cjk_font(&cc.egui_ctx);
            Box::new(PreviewApp::new(rx))
        }),
    )
}
