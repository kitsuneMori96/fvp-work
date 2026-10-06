# fvp-work 项目约定

## 下载优先中国镜像

所有下载（工具链、依赖、包）优先走中国镜像源，写死在项目里，不依赖个人机器配置：

- cargo 依赖：`fvp-preview/.cargo/config.toml` 已写死 tuna sparse 镜像。
- rustup 工具链/组件：`export RUSTUP_DIST_SERVER=https://mirrors.tuna.tsinghua.edu.cn/rustup`
  （rustup 不支持按项目配置，每台机器 shell profile 里加一次）。
- 后续新增下载点（pip/npm/gh 等）同样优先清华/中科大镜像，并在本文件登记。

## 其他

- 提交前跑 lint/typecheck/测试（有的话）；中文 conventional commits。
- 二进制不进仓（`fvp-preview/target/` 已忽略），release 用附件发货。
- 构建产物目录用 ext4（`CARGO_TARGET_DIR` 指到 WSL 侧，D 盘 9P 太慢）。
