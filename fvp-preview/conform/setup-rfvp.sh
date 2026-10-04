#!/bin/bash
# bootstrap rfvp 上游稀疏检出（给 fvp-preview/conform 与 vm 用）。
#
# 在 <Code>/ 目录下运行（与 fvp-work 同级，产物为 ./rfvp-upstream）：
#   bash ../fvp-work/fvp-preview/conform/setup-rfvp.sh
#
# 前置：git, python3, cargo；字体桩需要任意一条有效 TTF
# （默认 /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf，可用环境变量 TTF_STUB 覆盖）。
# 网络慢时给 cargo 配代理（示例 Clash）：export https_proxy=http://<winip>:7890 http_proxy=...
set -e
cd "$(dirname "$0")/../../.."
[ -d rfvp-upstream ] || git clone --depth 1 --filter=blob:none --sparse https://github.com/xmoezzz/rfvp.git rfvp-upstream
cd rfvp-upstream
git sparse-checkout set --no-cone \
  'Cargo.toml' 'Cargo.lock' 'crates/*/Cargo.toml' \
  'crates/rfvp/*' 'crates/rfvp-bitmap/*' 'crates/nvsg_pack/*' \
  'crates/assembler/*' 'crates/disassembler/*' \
  'crates/anzu-hal/*' 'crates/na_mpeg2_decoder/*' 'crates/na_wmv_player/*' \
  '!crates/rfvp/src/subsystem/resources/fonts/*'

# 工作区收窄：稀疏检出下全量 members 无法解析（各 crate 无 src）。
python3 - <<'EOF'
s = open('Cargo.toml').read()
old = '"crates/*",'
new = ('"crates/rfvp",\n    "crates/rfvp-bitmap",\n    "crates/nvsg_pack",\n'
       '    "crates/assembler",\n    "crates/disassembler",\n    "crates/anzu-hal",\n'
       '    "crates/na_mpeg2_decoder",\n    "crates/na_wmv_player",')
assert old in s, "上游 Cargo.toml members 已变，手动处理"
open('Cargo.toml', 'w').write(s.replace(old, new, 1))
print("workspace members narrowed")
EOF

# 工具链降特性：disassembler/assembler 不需要 gstreamer 系 native-video。
sed -i 's|rfvp = { path = "../rfvp" }|rfvp = { path = "../rfvp", default-features = false, features = ["soft-render-core"] }|' \
  crates/assembler/Cargo.toml crates/disassembler/Cargo.toml

# scratch patches（专用，不进上游）：按文件名顺序全打，有则跳过。
for PATCH in ../fvp-work/fvp-preview/conform/rfvp-patches/*.patch; do
  [ -f "$PATCH" ] || continue
  if git apply --check "$PATCH" 2>/dev/null; then
    git apply "$PATCH" && echo "patch applied: $(basename "$PATCH")"
  else
    echo "patch already applied, skip: $(basename "$PATCH")"
  fi
done

# 字体桩：28MB MS 字体被稀疏排除（include_bytes! 需要文件存在）。
# headless conformance 不渲染文字，任意有效 TTF 顶替即可。
TTF_STUB="${TTF_STUB:-/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf}"
[ -f "$TTF_STUB" ] || { echo "ERR: TTF stub not found: $TTF_STUB (set \$TTF_STUB)" >&2; exit 1; }
mkdir -p crates/rfvp/src/subsystem/resources/fonts
for f in MSGOTHIC.TTF MSMINCHO.TTF MS-PGothic.ttf MS-PMincho-2.ttf; do
  [ -f "crates/rfvp/src/subsystem/resources/fonts/$f" ] || cp "$TTF_STUB" "crates/rfvp/src/subsystem/resources/fonts/$f"
done

echo "=== 校验构建 ==="
cargo check -p rfvp --no-default-features --features soft-render-core 2>&1 | tail -n 2
cargo build -p disassembler -p assembler 2>&1 | tail -n 2
echo "setup-rfvp done"
