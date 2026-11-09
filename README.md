# 译见 · Yijian

> 遇见论文的中文一面。一个跨平台的桌面双语论文阅读器。

输入 arXiv ID，取回 LaTeX 源码，逐段翻译，再用 ctex 重新编译成中英对照的 PDF。翻译走你自己配置的 OpenAI 兼容端点（BYOK，自带 Key），公式、引用、图表编号都留在原位。

- **本地优先**：论文、译文、笔记、文库都存在你自己的电脑上，不上传任何服务器。
- **不改原文结构**：以 LaTeX 源码为输入，用占位符保护公式与命令，重编译而不是拼接 Markdown。
- **不用装环境**：引擎和 tectonic 都随安装包分发，用户不需要 Python 或 TeX Live。

## 下载

从 [Releases](https://github.com/cpypy/yijian/releases) 下载最新版本：

| 平台 | 文件 |
|---|---|
| Windows 10/11 | `Yijian_<版本>_x64-setup.exe` |
| macOS（Apple Silicon） | `Yijian_<版本>_aarch64.dmg` |
| Linux（Debian / Ubuntu） | `Yijian_<版本>_amd64.deb` |

安装包**未做代码签名**：Windows 首次运行会弹 SmartScreen（「更多信息 → 仍要运行」），macOS 需在「系统设置 → 隐私与安全性」里放行，Linux 用 `dpkg -i` 安装。

## 现在能做什么

| 模块 | 状态 | 说明 |
|---|---|---|
| 翻译链路 | ✅ 可用 | 取源 → 分段 → 占位符保护 → LLM 翻译 → ctex 注入 → tectonic 编译 |
| 自带模型（BYOK） | ✅ 可用 | 设置页填 Base URL / 模型 / Key，支持任意 OpenAI 兼容端点；不填 Key 时用占位译文，链路照样跑通 |
| 双语阅读器 | ✅ 可用 | 原文 / 对照 / 译文三种模式、文内搜索、KaTeX 公式、引用卡片、术语高亮、段落笔记（自动保存） |
| 文库 | ✅ 可用 | 标题 / 作者 / 阅读进度、集合树、跨论文全文搜索 |
| 发现 | ✅ 可用 | alphaXiv 近 7 天热榜，一键译入 |
| PDF 预览 | 🚧 打磨中 | 目前用系统默认阅读器打开，应用内嵌视图还在做 |
| 代码签名 / 自动更新 | ⏳ 未开始 | |

> 版本号以 [Releases](https://github.com/cpypy/yijian/releases) 和 `package.json` 为准；`main` 分支通常比最近一次发布更新。

## 架构

```
┌──────────────────────────────────────────────┐
│  Tauri 2 窗口（Rust + Svelte 5）              │
│  工作台 / 队列 / 阅读器 / 文库 / 设置          │
└───────────────────┬──────────────────────────┘
                    │ stdio（JSON Lines 事件）
┌───────────────────▼──────────────────────────┐
│  sidecar：yijian-engine（Python / PyInstaller）│
│  arXiv 取源 → 分段 → 占位符保护 → LLM 翻译     │
│  → ctex 注入 → tectonic 编译 → segments.json  │
└───────────────────┬──────────────────────────┘
                    │ 127.0.0.1 随机端口（本地）
┌───────────────────▼──────────────────────────┐
│  texlate server（翻译与编译管线，复用上游）     │
└──────────────────────────────────────────────┘
```

- **壳**：Tauri 2。Rust 只负责窗口、子进程管理与事件转发，界面用 Svelte 5 + Vite。
- **引擎**：Python 3.12，复用 [texlate](https://github.com/WncFht/texlate) 的管线（以依赖方式引入），用 PyInstaller 打成独立可执行文件随包分发。
- **编译器**：tectonic 单文件二进制；安装包预置约 33 MB 宏包缓存，缺的宏包首次编译时按需下载。
- **数据**：Windows 安装版写入 `%LOCALAPPDATA%\Yijian\`；macOS/Linux 的落点尚未统一到系统标准目录（已知问题），源码运行则写在仓库内 `data/`。

## 仓库结构

```
src/            Svelte 前端（App.svelte 为主）
src-tauri/      Tauri 2 应用（Rust）
sidecar/        Python 引擎入口与 PyInstaller spec
app/            早期本地 API / 引擎实现（分段、任务、SQLite；保留参考）
web/            早期无构建前端（保留参考）
tools/          依赖引导、CI、打包辅助脚本
docs/           设计方案、踩坑记录、界面原型、问题清单
packaging/      旧版 Qt 打包配置（保留参考）
assets/         预置 TeX 宏包缓存（texbundle.tar.gz）
```

## 构建

本地需要 **Rust 1.77+、Node 20+、Python 3.12**，以及各平台的 Tauri 系统依赖（Linux 见 `.github/workflows/build.yml`）。

```bash
npm install

# 先出引擎（sidecar 以子进程方式被壳拉起）
python -m PyInstaller sidecar/yijian-engine.spec --noconfirm \
    --distpath dist-sidecar --workpath .build/work-sidecar

# 开发（热更新前端）
npm run tauri dev

# 出安装包
npm run tauri build
```

本机没有 Rust / MSVC 时**交给 CI**：推一个 `v*` 标签，GitHub Actions 会在 Windows / macOS / Linux 三个 runner 上分别构建引擎、打包安装包并创建 Release。

```bash
git tag -a v0.1.7 -m "译见 0.1.7"
git push origin v0.1.7
```

## 常见问题

- **首次编译很慢**：安装包要展开预置宏包缓存（600+ 个文件，杀软逐个扫描会拖时间），界面日志会显示进度。
- **翻译被截断**：日志出现 `finish_reason=length` 说明单批输出超出模型上限。换上下文更大的模型，或设置 `YIJIAN_BATCH_CHARS=2000` 后重试。
- **模型 502 / 401**：检查 Base URL、模型名与 Key 是否匹配；部分端点不支持 `thinking` 参数。
- **Windows 上 texlate 水土不服**：env 白名单缺 `SYSTEMROOT`、`mkdir(mode=0o700)` 收紧 ACL，sidecar 启动时在运行时打补丁，不需要改安装文件。

## 相关文档

- [设计方案](docs/设计方案.md) —— 产品定位、架构、数据模型、里程碑
- [踩坑记录](docs/踩坑记录.md) —— Windows 权限、打包、CI 上踩过的坑与修法
- [问题清单](docs/问题清单.md) —— 已知问题与修复记录
- [界面原型](docs/界面原型-v2.html) —— 可点击的三视图稿（浏览器打开）
- [发版说明](docs/RELEASE-0.1.0.md) —— v0.1.0 的功能与限制

## 参与贡献

Issue 和 PR 都欢迎，先读 [贡献指南](CONTRIBUTING.md)。安全问题请走 [安全策略](SECURITY.md) 里的私下报告渠道，不要开公开 Issue。

## 许可证

本项目以 [Apache-2.0](LICENSE) 许可发布，版权归 Yijian contributors 所有。上游项目与第三方组件见 [NOTICE](NOTICE)；许可证另有[非官方中文说明](LICENSE.zh-CN.md)（仅供理解，以英文原文为准）。

## 版本策略

- 只在「一个完整功能可用」时打 tag 发版，不为单次改动发版。
- 发版失败的 tag 直接删除，不占用编号。
- 一个编号对应一个可用的里程碑；日常改动只推 commit。
