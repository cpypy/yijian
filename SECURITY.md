# 安全策略

## 报告漏洞

请用 GitHub 的 [Private Vulnerability Reporting](../../security/advisories/new)，不要开公开 Issue。

## 威胁模型

### 1. API Key

- 目标：Key 只存在于 Windows 凭据管理器，**不写数据库、不进日志、不进崩溃报告**。
- 当前：W1 还没有配置界面，也没有任何 Key 落盘。
- 约束：`data/` 下的任何文件都不得包含 Key；日志里出现的请求头必须脱敏。

### 2. 本地 HTTP 服务

- 只监听 `127.0.0.1`，端口随机。
- 计划加入启动时生成的 Bearer Token 与 Origin 校验，防止同机其他进程调用。
- 当前 W1：仅监听环回、无鉴权，不要暴露到局域网。

### 3. 不可信的 LaTeX

这是本项目最需要小心的地方。论文源码来自外部，而 LaTeX 在开启 shell-escape 时可以执行任意命令。

- tectonic 默认不开启 shell-escape，保持默认。
- 若走 xelatex 通道，必须显式加 `-no-shell-escape`。
- 不编译用户未确认来源的 `.tex`；`\write18` 一律视为可疑。

### 4. 第三方接口

- alphaXiv 接口无官方文档，按其条款只读公开数据，不抓全文、不绕过鉴权。
- 不把用户文库、阅读记录上传到任何服务器；所有推荐计算在本地完成。

## 支持范围

只维护最新发布版本。W1 阶段没有正式发布，安全问题请直接提 Private Advisory。
