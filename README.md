# 译见 · Yijian

> 遇见论文的中文一面。一个跨平台桌面双语论文阅读器。

输入 arXiv ID，取回 LaTeX 源码，逐段翻译，用 ctex 重新编译成中英对照 PDF。翻译走你自己的 OpenAI 兼容端点（BYOK），按量计费；公式、引用、图表编号保持原位。

## 下载

从 [Releases](https://github.com/cpypy/yijian/releases) 下载：

| 平台 | 文件 |
|---|---|
| Windows 10/11 | `Yijian_0.1.2_x64-setup.exe` |
| macOS (Apple Silicon) | `Yijian_0.1.2_aarch64.dmg` |
| Linux (Debian/Ubuntu) | `Yijian_0.1.2_amd64.deb` |

安装包未签名，首次运行可能需要 SmartScreen 确认。

**状态：v0.1.2，能装能跑，但引擎链路还在验证。** 目前是「输入 arXiv ID → 取源 → mock 占位翻译 → 编译 → 打开 PDF」，真翻译（BYOK）和阅读器尚未接入。

## 架构

```
┌─────────────────────────────────────────────┐
│  Tauri 2 窗口（Rust + Svelte）               │
│  任务列表 / 进度 / 日志 / PDF 预览            │
└──────────────────┬──────────────────────────┘
                   │ stdio（JSON Lines 事件）
┌──────────────────▼──────────────────────────┐
│  sidecar：yijian-engine（Python，PyInstaller）│
│  arXiv 取源 → 分段 → 占位符保护 → LLM 翻译    │
│  → ctex 注入 → tectonic 编译 → dual.json     │
└─────────────────────────────────────────────┘
```

- **壳**：Tauri 2，Rust 只负责窗口、进程管理和事件转发；界面用 Svelte。
- **引擎**：Python（复用 [texlate](https://github.com/WncFht/texlate) 的管线），打包成独立可执行文件随安装包分发，**用户不需要装 Python**。
- **编译器**：tectonic（单文件二进制，随包分发），首次编译时按需下载 TeX bundle。

## 仓库结构

```
src/            Svelte 前端
src-tauri/      Tauri 2 应用（Rust）
sidecar/        Python 引擎与 PyInstaller 配置
app/            引擎的 Python 实现（分段、任务、SQLite）
docs/           设计方案与界面原型
packaging/      旧版 Qt 打包配置（保留参考）
```

## 构建

本地需要 Rust 1.77+、Node 20+、Python 3.12。三者齐备后：

```bash
npm install
npm run tauri dev          # 开发
python -m PyInstaller sidecar/yijian-engine.spec   # 先出引擎
npm run tauri build        # 出安装包
```

如果本机没有 Rust/MSVC，**交给 CI**：推送一个 `v*` 标签，GitHub Actions 会在
Windows / macOS / Linux 三个 runner 上分别构建引擎、打包安装包并创建 Release。

```bash
git tag -a v0.1.1 -m "译见 0.1.1"
git push origin v0.1.1
```

## 当前进度

| 模块 | 状态 |
|---|---|
| 引擎链路（取源 → 分段 → mock 翻译 → ctex → tectonic） | ✅ 产出 1.3 MB PDF（arXiv:1706.03762） |
| sidecar 打包（stdio 事件协议） | ✅ 可独立运行 |
| Tauri 壳 | 🚧 骨架完成，待 CI 首次构建 |
| 真翻译（BYOK） | ⏳ |
| 阅读器 / 文库 / 发现 | ⏳ |

## 已知限制

- Windows 上 texlate 有两处水土不服（env 白名单缺 `SYSTEMROOT`、`mkdir(mode=0o700)` 收紧 ACL），sidecar 在启动时于运行时打补丁，不需要改安装文件。
- 首次编译需要联网拉 TeX bundle（约 60 MB），之后走本地缓存。
- alphaXiv 的公开接口没有官方文档，发现页按「非官方接口」对待。

## 相关文档

- [设计方案](docs/设计方案.md) —— 产品定位、架构、数据模型、里程碑
- [踩坑记录](docs/踩坑记录.md) —— Windows 权限、打包、CI 上踩过的坑与修法
- [界面原型](docs/界面原型.html) —— 可点击的三视图稿（浏览器打开）

## 许可证

[Apache-2.0](LICENSE)。上游项目与第三方组件见 [NOTICE](NOTICE)。
