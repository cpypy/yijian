# 贡献指南

先说结论：**现在最需要的是 Issue，不是 PR。** 项目在 W1 阶段，接口和结构还会变，大改动请先开 Issue 对一下方向。

## 开发环境

- Windows 10/11，Python 3.12
- 依赖不打进系统环境，解压到仓库内 `.runtime/`：

```bat
python tools\bootstrap_deps.py
tools\install_tectonic.bat
tools\doctor.bat
```

- 启动：`run.bat`（窗口）或 `python -m app.main --headless`（只起 API）
- 运行前端：改 `web/index.html` 后刷新窗口即可，无构建步骤

## 代码风格

- Python 3.12，类型注解能写就写；`ruff check .` 必须通过（行宽 100）
- 前端在 W1 阶段是原生 JS，不引框架；等阅读器开工再统一到 React
- 注释写「为什么」，不写「做了什么」

## 提交信息

```
feat: 段落级重译
fix: tectonic 缓存目录写到项目内
docs: 补充 BYOK 配置说明
refactor: 抽 EnginePort 接口
```

## Pull Request

1. 先开 Issue，说明要解决什么问题
2. 一个 PR 只做一件事；顺手改格式的 PR 会被打回
3. 附上验证方式：跑了哪篇论文、编译成功还是失败、日志在哪
4. CI 必须绿

## 报告编译问题

LaTeX 编译失败是最常见的 bug。提 Issue 时请附：

- arXiv ID
- `data/jobs/<job_id>/run.log` 的最后 80 行
- `texlate doctor` 的输出
- 你的 Windows 版本

## 不要提交

- `data/`：论文原文、PDF 产物有版权，且体积大
- `.runtime/`、`.wheels/`：依赖产物
- 任何 API Key、代理地址、个人路径
