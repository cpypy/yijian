# 译见 · Yijian

> 遇见论文的中文一面。一个 Windows 桌面双语论文阅读器。

把「发现论文 → 收进文库 → 一键中英对照精读」连成一条线的桌面应用。翻译走你自己的 OpenAI 兼容端点（BYOK），按量计费；排版复用论文的 LaTeX 源码重新编译，公式、引用、图表编号保持原位。

**当前状态：W1（骨架期），还不能日常使用。** 引擎链路已在 Windows 上跑通到编译前一步，界面是单页原型。

## 为什么做这个

我长期用「幻觉翻译」读论文，后来它开始收费。社区里 [texlate](https://github.com/WncFht/texlate) 做了同样思路的开源实现（arXiv LaTeX 源码 → 段落级翻译 → ctex 重编译），但它是一个 Web 服务：要起进程、开浏览器、手动提交任务。

译见想做的是**桌面应用**：双击打开、拖入论文、本地文库、离线可读、系统级集成。

## 和 texlate 的关系

- **核心思路完全来自 texlate**，并在其思路上做桌面化封装。
- 当前版本以依赖方式使用 texlate（`texlate==0.1.1`，Apache-2.0）。
- 后续计划把引擎 fork 进本仓库（`vendor/texlate`），以便加入段落级重译、优先级队列、人工修订回写 PDF 等桌面端必需的能力。
- texlate 采用 Apache-2.0，本项目同样采用 Apache-2.0，详见 [NOTICE](NOTICE)。

## 当前进度

| 模块 | 状态 |
|---|---|
| 引擎链路（取源 → 分段 → 占位翻译 → 注入 ctex） | ✅ 已跑通（arXiv:1706.03762） |
| tectonic 编译 | ⚠️ 缓存目录受环境限制，待修（见 Issues） |
| 本地 API + SQLite 任务表 | ✅ |
| 单页界面（输入 ID、进度、日志） | ✅ 原型 |
| 桌面窗口（WebView2） | ⏳ 待接入 |
| 真翻译（BYOK） | ⏳ W3 |
| 文库 / 发现（alphaXiv 全站热榜） | ⏳ |

## 快速开始

需要 Windows + Python 3.12（本机用 Anaconda 的 3.12.7 验证）。

```bat
:: 1. 安装依赖（不走 pip 安装到环境，而是解压到 .runtime）
python tools\bootstrap_deps.py

:: 2. 安装 TeX 引擎（tectonic，约 30 MB）
tools\install_tectonic.bat

:: 3. 自检
tools\doctor.bat

:: 4. 启动
run.bat
```

`run.bat` 默认开窗口；`python -m app.main --headless` 只起本地 API，浏览器访问终端打印的地址。

### 目录约定

```
app/          后端：FastAPI + SQLite + 引擎适配层
web/          前端：单页界面（W1 用原生 JS，后续换 React）
tools/        安装、自检、研究脚本
docs/         设计文档与界面原型
data/         运行时数据（论文缓存、任务产物、数据库）——不入库
.runtime/     依赖解压目录——不入库
```

## 技术栈

Python 3.12 · FastAPI · SQLite · pywebview（WebView2，待接入）· tectonic · ctex

## 路线图

- **W1** 骨架：窗口 + 本地 API + arXiv 导入 + mock 编译
- **W2** 阅读器：双语对照、公式渲染、同步滚动
- **W3** 真翻译：BYOK、SSE 流式、段落状态、缓存
- **W4** 文库：集合、标签、智能集合、全文搜索
- **W5** 发现：alphaXiv 全站热榜
- **W6+** 打包、签名、自动更新

详细设计见 [docs/设计方案.md](docs/设计方案.md)，可点击的界面原型见 [docs/界面原型.html](docs/界面原型.html)。

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。如果你也读英文论文、也在被订阅制收费困扰，欢迎来提 Issue 或 PR。

## 免责声明

- 本项目**不是** alphaXiv / arXiv / texlate 的官方项目，与它们没有隶属关系。
- alphaXiv 的公开接口没有官方文档，客户端按「非官方接口」对待，随时可能失效。
- 请只翻译你有权访问的论文，产物仅供个人学习研究使用，不要二次分发原文。

## 许可证

[Apache-2.0](LICENSE)。第三方组件与上游项目的许可见 [NOTICE](NOTICE)。
