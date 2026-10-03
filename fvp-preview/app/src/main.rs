//! fvp-preview: HCB 整场景预览窗 + 可视化编辑器（M2 A 轨）。
//!
//! 输入：stdin 行分隔 JSON 快照（见 [`Snapshot`]），编辑器光标每动一次推一行，
//! 窗口 1 帧内刷新。渲染数学全部走 `fvp-preview-core`（与 rfvp 同一份语义）。
//!
//! 编辑用法：
//! ```sh
//! fvp-preview-vm Sakura.hcb --ticks 2000 --png-dir /tmp/vmtex > scene.json
//! cat scene.json | fvp-preview --addrmap traced_addrmap.json \
//!   --hcb Sakura.hcb --out-hcb Sakura_edit.hcb
//! ```
//! 点击选中 prim → 拖拽改 x/y → 右侧面板改数值 → “写回 HCB”调 patch_hcb.py
//! 生成新 HCB（只改等宽 push_* 立即数；无源字段标红拒绝）。
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
    /// 编辑面板字段表：(名, 取值, 单位提示)。
    fn field(&self, name: &str) -> Option<(i64, &'static str)> {
        let v = match name {
            "x" => (self.x as i64, "px 整数"),
            "y" => (self.y as i64, "px 整数"),
            "z" => (self.z as i64, "100..10000(透视除数)"),
            "angle" => (self.angle as i64, "十分之一度%3600"),
            "fx" => (self.fx as i64, "千分比"),
            "fy" => (self.fy as i64, "千分比"),
            "attr" => (self.attr as i64, "bit:2=OP,4=透视"),
            "opx" => (self.opx as i64, "px(需attr&2)"),
            "opy" => (self.opy as i64, "px(需attr&2)"),
            "alpha" => (self.alpha as i64, "0..255"),
            _ => return None,
        };
        Some(v)
    }
    fn set_field(&mut self, name: &str, v: i64) {
        match name {
            "x" => self.x = v as i32,
            "y" => self.y = v as i32,
            "z" => self.z = v as i32,
            "angle" => self.angle = v as i32,
            "fx" => self.fx = v as i32,
            "fy" => self.fy = v as i32,
            "attr" => self.attr = v as i32,
            "opx" => self.opx = v as i32,
            "opy" => self.opy = v as i32,
            "alpha" => self.alpha = v.clamp(0, 255) as u8,
            _ => {}
        }
    }
}

const EDIT_FIELDS: &[&str] = &[
    "x", "y", "z", "angle", "fx", "fy", "attr", "opx", "opy", "alpha",
];

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
    /// 当前窗口匹配的 viewport（变化时发 resize，保证画布与游戏同分辨率 1:1）。
    win_vp: Option<(f32, f32)>,
    /// 编辑状态
    selected: Option<i32>,
    /// 快照原始值（新快照到达时重建）：(id, field) -> 原值。
    orig: HashMap<(i32, String), i64>,
    /// 写回映射存在性：(id, field) -> 有常量源可改。
    mappable: HashMap<(i32, String), bool>,
    addrmap_path: Option<String>,
    hcb_path: Option<String>,
    out_hcb_path: Option<String>,
    patch_status: String,
}

impl PreviewApp {
    fn new(
        rx: Receiver<Snapshot>,
        addrmap_path: Option<String>,
        hcb_path: Option<String>,
        out_hcb_path: Option<String>,
    ) -> Self {
        let mappable = addrmap_path
            .as_deref()
            .and_then(|p| std::fs::read_to_string(p).ok())
            .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
            .map(|v| {
                let mut m = HashMap::new();
                if let Some(am) = v.get("addrmap").and_then(|a| a.as_object()) {
                    for (pid, fields) in am {
                        if let (Ok(id), Some(obj)) =
                            (pid.parse::<i32>(), fields.as_object())
                        {
                            for f in obj.keys() {
                                m.insert((id, f.clone()), true);
                            }
                        }
                    }
                }
                m
            })
            .unwrap_or_default();
        Self {
            rx,
            scene: None,
            textures: HashMap::new(),
            status: "等待快照（stdin JSON）…".to_string(),
            win_vp: None,
            selected: None,
            orig: HashMap::new(),
            mappable,
            addrmap_path,
            hcb_path,
            out_hcb_path,
            patch_status: String::new(),
        }
    }

    fn poll_stdin(&mut self, ctx: &egui::Context) {
        let mut updated = false;
        while let Ok(snap) = self.rx.try_recv() {
            self.scene = Some(snap);
            updated = true;
        }
        if updated {
            // 新快照：重建原始值表，选中保留（按 id），跨快照的编辑丢弃并提示。
            self.orig.clear();
            if let Some(scene) = &self.scene {
                for p in &scene.prims {
                    for f in EDIT_FIELDS {
                        if let Some((v, _)) = p.field(f) {
                            self.orig.insert((p.id, f.to_string()), v);
                        }
                    }
                }
                if let Some(sel) = self.selected {
                    if !scene.prims.iter().any(|p| p.id == sel) {
                        self.selected = None;
                    }
                }
            }
            let n = self.scene.as_ref().map(|s| s.prims.len()).unwrap_or(0);
            self.status = format!("已加载快照：{} prim", n);
            ctx.request_repaint();
        }
        // 窗口分辨率跟随游戏 viewport（HCB 可视化编辑器铁律：1:1 同分辨率）。
        if let Some(scene) = &self.scene {
            let vp = (scene.viewport.w, scene.viewport.h);
            if self.win_vp != Some(vp) && vp.0 > 0.0 && vp.1 > 0.0 {
                self.win_vp = Some(vp);
                // +34 为顶部状态栏高度；超出屏幕时用户可缩放窗口，画布走滚动。
                ctx.send_viewport_cmd(egui::ViewportCommand::InnerSize(egui::Vec2::new(
                    vp.0,
                    vp.1 + 34.0,
                )));
                let n = self.scene.as_ref().map(|s| s.prims.len()).unwrap_or(0);
                self.status = format!("已加载快照：{} prim（{}x{}）", n, vp.0 as u32, vp.1 as u32);
            }
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

    /// 点击命中：按绘制序自顶向下，点进任一 quad 即选中（用逆变换判局部矩形）。
    fn hit_test(
        &mut self,
        ctx: &egui::Context,
        scene: &Snapshot,
        cam: &Camera,
        vp: &Viewport,
        origin: egui::Pos2,
        pos: egui::Pos2,
    ) -> Option<i32> {
        let lx = pos.x - origin.x;
        let ly = pos.y - origin.y;
        let mut order = Self::draw_order(scene);
        order.reverse();
        for (i, px, py) in order {
            let prim = &scene.prims[i];
            if prim.image.is_empty() {
                continue;
            }
            let Some((_, w, h)) = self.texture_for(ctx, &prim.image) else {
                continue;
            };
            let m = model::build_model(&prim.params(), px, py, cam, vp);
            let Some(inv) = m.inverse() else { continue };
            let local = inv.transform_pos(CorePos2::new(lx, ly));
            if local.x >= 0.0 && local.y >= 0.0 && local.x <= w && local.y <= h {
                return Some(prim.id);
            }
        }
        None
    }

    /// 当前场景相对原始值的改动：(id, field, 新值)。
    fn pending_edits(&self, scene: &Snapshot) -> Vec<(i32, String, i64)> {
        let mut out = Vec::new();
        for p in &scene.prims {
            for f in EDIT_FIELDS {
                if let (Some((v, _)), Some(&o)) = (p.field(f), self.orig.get(&(p.id, f.to_string()))) {
                    if v != o {
                        out.push((p.id, f.to_string(), v));
                    }
                }
            }
        }
        out
    }

    /// 调 patch_hcb.py 写回（等宽约束由脚本强制；返回状态文本）。
    fn run_writeback(&self, edits: &[(i32, String, i64)]) -> String {
        let (Some(hcb), Some(am), Some(out)) =
            (&self.hcb_path, &self.addrmap_path, &self.out_hcb_path)
        else {
            return "写回需要 --hcb/--addrmap/--out-hcb 三个参数".to_string();
        };
        let unmapped: Vec<String> = edits
            .iter()
            .filter(|(id, f, _)| !self.mappable.contains_key(&(*id, f.clone())))
            .map(|(id, f, _)| format!("{}.{}", id, f))
            .collect();
        if !unmapped.is_empty() {
            return format!("拒绝：无常量源（动态/歧义）：{}", unmapped.join(", "));
        }
        let mut cmd = std::process::Command::new("python3");
        // patch 脚本路径：环境变量优先，否则 PATH 里找 patch_hcb.py。
        cmd.arg(
            std::env::var("FVP_PATCH_SCRIPT").unwrap_or_else(|_| "patch_hcb.py".to_string()),
        );
        cmd.arg(hcb).arg(am).arg(out);
        for (id, f, v) in edits {
            cmd.arg(format!("{}:{}={}", id, f, v));
        }
        match cmd.output() {
            Ok(o) => {
                let tail = String::from_utf8_lossy(&o.stdout).to_string()
                    + &String::from_utf8_lossy(&o.stderr);
                if o.status.success() {
                    format!("写回成功 -> {} | {}", out, tail.lines().last().unwrap_or(""))
                } else {
                    format!("写回失败：{}", tail.lines().last().unwrap_or("?"))
                }
            }
            Err(e) => format!("启动 patch 失败：{}（设 FVP_PATCH_SCRIPT 指定脚本路径）", e),
        }
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
                ui.label("fvp-preview（A轨快照+编辑）");
                ui.separator();
                ui.label(&self.status);
                if !self.patch_status.is_empty() {
                    ui.separator();
                    ui.label(&self.patch_status);
                }
            });
        });

        // 右侧编辑面板（选中 prim 的数值字段 + 写回按钮）。
        egui::SidePanel::right("editor")
            .default_width(220.0)
            .show(ctx, |ui| {
                ui.heading("编辑");
                let Some(scene) = self.scene.clone() else {
                    ui.label("无快照");
                    return;
                };
                let Some(sel) = self.selected else {
                    ui.label("点击画布选中 prim");
                    return;
                };
                let Some(pos) = scene.prims.iter().position(|p| p.id == sel) else {
                    ui.label("选中已失效");
                    return;
                };
                ui.label(format!("prim #{}（拖画布改x/y）", sel));
                ui.separator();
                // 借出 scene 可变：改完直接写回 self.scene。
                let mut changed = false;
                {
                    let scene_mut = self.scene.as_mut().unwrap();
                    let prim = &mut scene_mut.prims[pos];
                    for f in EDIT_FIELDS {
                        let Some((v, unit)) = prim.field(f) else { continue };
                        let mapped = self.mappable.contains_key(&(sel, f.to_string()));
                        let mut nv = v;
                        ui.horizontal(|ui| {
                            // 无源字段标红：改了也写不回去，提前告诉用户。
                            if mapped {
                                ui.label(*f);
                            } else {
                                ui.colored_label(egui::Color32::RED, *f);
                            }
                            ui.add(
                                egui::DragValue::new(&mut nv)
                                    .speed(1.0)
                                    .custom_formatter(|n, _| format!("{}", n as i64))
                                    .custom_parser(|s| s.parse::<f64>().ok()),
                            );
                        });
                        ui.small(unit);
                        if nv != v {
                            prim.set_field(f, nv);
                            changed = true;
                        }
                    }
                }
                if changed {
                    ctx.request_repaint();
                }
                ui.separator();
                let scene_ref = self.scene.as_ref().unwrap();
                let edits = self.pending_edits(scene_ref);
                ui.label(format!("待写回 {} 项", edits.len()));
                for (id, f, v) in edits.iter().take(12) {
                    let o = self.orig.get(&(*id, f.clone())).cloned().unwrap_or(0);
                    ui.small(format!("#{} {}: {} -> {}", id, f, o, v));
                }
                if ui.button("写回 HCB").clicked() {
                    self.patch_status = self.run_writeback(&edits);
                }
                ui.small("红字段=无常量源，写回会拒绝；改完重跑 vm 验证。");
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
            // 游戏分辨率画布（1:1），窗口装不下时滚动而非缩放——编辑器所见即游戏所得。
            egui::ScrollArea::both()
                .auto_shrink([false, false])
                .show(ui, |ui| {
                    let (canvas_rect, canvas_resp) = ui.allocate_exact_size(
                        egui::Vec2::new(vp.w, vp.h),
                        egui::Sense::click_and_drag(),
                    );
                    let origin = canvas_rect.min;
                    // 点击选中（顶层优先）；拖拽选中项改 x/y（1:1，屏差即像素差）。
                    if canvas_resp.clicked() {
                        if let Some(p) = canvas_resp.interact_pointer_pos() {
                            self.selected =
                                self.hit_test(ctx, &scene, &cam, &vp, origin, p);
                        }
                    }
                    if let (Some(sel), true) = (self.selected, canvas_resp.dragged()) {
                        let d = canvas_resp.drag_delta();
                        if d.x != 0.0 || d.y != 0.0 {
                            if let Some(s) = self.scene.as_mut() {
                                if let Some(p) = s.prims.iter_mut().find(|p| p.id == sel) {
                                    p.x += d.x.round() as i32;
                                    p.y += d.y.round() as i32;
                                    ctx.request_repaint();
                                }
                            }
                        }
                    }
                    let painter = ui.painter_at(canvas_rect);
                    // 选中框：选中 prim 的 quad 描边。
                    let mut sel_corners: Option<[CorePos2; 4]> = None;
                    for (i, px, py) in Self::draw_order(&scene) {
                        let prim = &scene.prims[i];
                        let Some((tex_id, w, h)) = self.texture_for(ctx, &prim.image) else {
                            continue;
                        };
                        let corners = model::quad_corners(&prim.params(), w, h, px, py, &cam, &vp);
                        if Some(prim.id) == self.selected {
                            sel_corners = Some(corners);
                        }
                        let tint =
                            egui::Color32::from_rgba_unmultiplied(255, 255, 255, prim.alpha);
                        let to_egui =
                            |c: CorePos2| egui::Pos2::new(origin.x + c.x, origin.y + c.y);
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
                    if let Some(cs) = sel_corners {
                        let pts: Vec<egui::Pos2> = cs
                            .iter()
                            .map(|c| egui::Pos2::new(origin.x + c.x, origin.y + c.y))
                            .collect();
                        painter.add(egui::Shape::closed_line(
                            pts,
                            egui::Stroke::new(2.0, egui::Color32::YELLOW),
                        ));
                    }
                });
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
    let mut addrmap_path: Option<String> = None;
    let mut hcb_path: Option<String> = None;
    let mut out_hcb_path: Option<String> = None;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--addrmap" => addrmap_path = args.next(),
            "--hcb" => hcb_path = args.next(),
            "--out-hcb" => out_hcb_path = args.next(),
            other => {
                eprintln!("[fvp-preview] 忽略未知参数: {}", other);
            }
        }
    }
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
            Box::new(PreviewApp::new(rx, addrmap_path, hcb_path, out_hcb_path))
        }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn prim(id: i32, x: i32, y: i32) -> PrimSer {
        PrimSer {
            id,
            parent: None,
            draw: true,
            x,
            y,
            z: 1000,
            angle: 0,
            fx: 1000,
            fy: 1000,
            attr: 0,
            opx: 0,
            opy: 0,
            u: 0,
            v: 0,
            off_x: 0.0,
            off_y: 0.0,
            alpha: 255,
            image: "g.png".to_string(),
        }
    }

    #[test]
    fn field_roundtrip() {
        let mut p = prim(7, 10, 20);
        assert_eq!(p.field("x"), Some((10, "px 整数")));
        assert_eq!(p.field("angle"), Some((0, "十分之一度%3600")));
        assert_eq!(p.field("nope"), None);
        p.set_field("x", 99);
        p.set_field("alpha", 300); // 钳制
        assert_eq!((p.x, p.alpha), (99, 255));
        p.set_field("alpha", -5);
        assert_eq!(p.alpha, 0);
    }

    #[test]
    fn pending_edits_diff() {
        let (_tx, rx) = mpsc::channel::<Snapshot>();
        let mut app = PreviewApp::new(rx, None, None, None);
        let mut scene = Snapshot {
            viewport: ViewportSer { w: 1280.0, h: 720.0 },
            camera: CameraSer::default(),
            prims: vec![prim(7, 10, 20), prim(8, 0, 0)],
        };
        for p in &scene.prims {
            for f in EDIT_FIELDS {
                if let Some((v, _)) = p.field(f) {
                    app.orig.insert((p.id, f.to_string()), v);
                }
            }
        }
        assert!(app.pending_edits(&scene).is_empty());
        scene.prims[0].set_field("x", 42);
        scene.prims[1].set_field("alpha", 128);
        let mut e = app.pending_edits(&scene);
        e.sort();
        assert_eq!(e, vec![(7, "x".to_string(), 42), (8, "alpha".to_string(), 128)]);
    }

    #[test]
    fn writeback_refuses_unmapped() {
        let (_tx, rx) = mpsc::channel::<Snapshot>();
        // 缺参数 -> 直接拒绝。
        let app = PreviewApp::new(rx, None, Some("a.hcb".into()), Some("b.hcb".into()));
        let msg = app.run_writeback(&[(7, "x".to_string(), 5)]);
        assert!(msg.contains("写回需要"), "got: {}", msg);
        // 参数齐但映射表读不到 -> 无源字段拒绝（还没调到脚本）。
        let (_tx2, rx2) = mpsc::channel::<Snapshot>();
        let app2 = PreviewApp::new(
            rx2,
            Some("/nonexistent.json".into()),
            Some("a.hcb".into()),
            Some("b.hcb".into()),
        );
        let msg2 = app2.run_writeback(&[(7, "x".to_string(), 5)]);
        assert!(msg2.contains("无常量源"), "got: {}", msg2);
    }
}
