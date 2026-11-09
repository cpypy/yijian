# 贡献指南

先说结论：**现阶段最需要的是 Issue，不是 PR。** 项目还在快速迭代，接口和目录结构随时会变，大改动请先开 Issue 对齐方向，避免白写。

## 开发环境

主力平台是 Windows 10/11，Python 3.12。依赖不装进系统环境，统一解压到仓库内的 `.runtime/`：

```bat
python tools\bootstrap_deps.py
install_tectonic.bat
```

需要 Rust 1.77+、Node 20+ 才能跑 Tauri 壳；只调引擎的话有 Python 就够。前端是 Svelte 5 + Vite，`npm install` 后 `npm run tauri dev` 即可热更新。

## 代码风格

- **Python 3.12**，能写类型注解就写；`ruff check .` 必须通过（配置见 `pyproject.toml`：行宽 100，规则 `E,F,I,UP,B`）。
- **前端**用 Svelte 5（runes 语法），不复用旧 `web/index.html` 的写法。
- **Rust** 侧只做窗口、进程管理和事件转发，业务逻辑放 Python 引擎，别往壳里堆。
- 注释写「为什么」，不写「做了什么」——踩过的坑尤其要写清楚。

## 提交信息

中英文都行，但保持 `<type>: <一句话>` 的格式，type 用下面几种：

```
feat: 段落级重译
fix: tectonic 缓存目录写到项目内
docs: 补充 BYOK 配置说明
refactor: 抽 EnginePort 接口
chore: 版本号归零到 0.1.0
ci: 同一 ref 只保留最新一次构建
```

一次提交只做一件事；格式化、重命名的顺手改动单独放一个 `style:` 提交。

## Pull Request

1. 先开 Issue，说明要解决什么问题、打算怎么改。
2. 一个 PR 只做一件事；夹带无关改动的 PR 会被打回。
3. 附上验证方式：跑了哪篇论文（arXiv ID）、编译成功还是失败、日志在哪。
4. CI 必须绿；UI 改动附截图或录屏。
5. 改动翻译管线或打包流程时，同步更新 `docs/` 里对应的文档。

## 报告编译问题

LaTeX 编译失败是最常见的 bug。提 Issue 时请附：

- arXiv ID（带版本号更好，如 `1706.03762v7`）
- `data/jobs/<job_id>/run.log` 的最后 80 行（安装版在 `%LOCALAPPDATA%\Yijian\data\jobs\...`）
- 你的操作系统版本、应用版本（设置页或 `package.json`）
- 用的模型端点名称（**不要贴 Key**）

## 不要提交

- `data/`：论文原文、PDF 产物有版权，且体积大
- `.runtime/`、`.deps/`、`.wheels/`、`node_modules/`、`dist*/`、`.build/`：依赖与构建产物
- `settings.json`：里面有 Key，明文
- 任何 API Key、代理地址、个人路径、内网地址

提交前跑一次 `git status` 和 `git diff --cached`，确认没有夹带敏感信息。
