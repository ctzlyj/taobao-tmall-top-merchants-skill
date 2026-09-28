# 企业数据源首次配置

仅适用于用户选择的API模式。用户要求不付费、API无额度或使用已登录网页时，先读 `browser-enterprise.md`，不要求配置Key或运行付费预检。

## 检测

正式任务要求以下项目全部通过：

1. `~/.codex/config.toml` 存在准确命名的 `qcc-company`，URL 为官方公司 MCP，并通过 `bearer_token_env_var = "QCC_AUTH"` 取凭证。
2. `~/.openclaw/skills/company-search-fengniao/scripts/tool.mjs` 已安装。
3. 企查查 Key 与风鸟 Key 均为用户自己的 Key，且分别通过轻量请求验证。

其他企业 MCP 只可作为补充，不能替代 `qcc-company`。风鸟公共额度不能替代私有 Key。任一项缺失、Key 无效、额度不足或服务不可用时拒绝执行招商任务。

## 获取双 Key

- 企查查 Key：<https://agent.qcc.com/profile/api-key>
- 风鸟 Key：<https://www.riskbird.com/center/apiKey>

必须同时提供两个 Key。引导用户在配置助手的隐藏输入中提交，不要求在聊天中粘贴完整凭证，不要让用户自行配置环境变量。Codex 不得复述 Key，也不得把 Key 放入命令参数、日志、工作簿、手册或 Git。

Codex 启动以下助手，再通过标准输入依次传入企查查 Key 与风鸟 Key：

```powershell
python scripts/configure_enterprise_keys.py
```

助手会自动兼容用户是否带 `Bearer` 前缀，将两个凭证写入 Windows 当前用户环境，并在 Codex 配置中只保存环境变量名称，不保存 Key 值。在用户明确授权付费API后运行 `bootstrap.ps1 -AllowPaidApi` 做真实可用性验证。仓库内企查查脚本会直接读取该用户级配置；风鸟命令统一通过 `scripts/run_fengniao.py --allow-paid-api` 启动，因此当前 Codex 无需重启。

## webcli Browser Bridge

预检会尝试运行 `webcli extension install`。若仍未连接，逐条告诉小白用户：

1. 在 Chrome 地址栏输入 `chrome://extensions` 并回车。
2. 打开页面右上角“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择预检打印的 `~/.webcli/extension` 准确目录。
5. 将 Browser Bridge 固定到 Chrome 工具栏。
6. 保持 Chrome 开启，再按Router核对所选浏览器连接；只有API模式另运行 `bootstrap.ps1 -AllowPaidApi`。

只有 `connectivity.ok=true` 且至少一个 profile 的 `extensionConnected=true` 才算连接成功；顶层 `ok=true` 不足以通过。

## 风鸟 Skill

官方包：<https://clawhub.ai/xinshu001/company-search-fengniao>。全局安装：

```powershell
npx -y openclaw@2026.7.1-2 skills install '@xinshu001/company-search-fengniao' --global --acknowledge-clawhub-risk
```

安装由预检自动完成。风鸟必须使用用户自己的 Key；公共额度不能替代。进入风鸟 Skill 目录后按以下顺序调用：

```powershell
python scripts/run_fengniao.py --allow-paid-api discover "企业基本信息"
python scripts/run_fengniao.py --allow-paid-api call biz_fuzzy_search --params '{"key":"企业或品牌中文名"}'
python scripts/run_fengniao.py --allow-paid-api call biz_basic_info --params '{"entid":"上一步内部ID"}'
```

先用 `biz_fuzzy_search` 消歧，再用同一个内部 `entid` 查询基本信息。企业简称或品牌出现多个候选时不得直接取第一条。`entid` 不写入工作簿。

## 数据纪律

- 运行 `scripts/company_source_routing.py`：模糊身份先风鸟、精确身份先企查查，随后用另一数据源核验或补缺
- 简称或品牌先实体识别
- 多候选由用户/店铺资质证据确认
- 完整公司名或信用代码才能查工商详情
- 联系方式不能单独确认店铺主体；仅有电话/邮箱的候选保持 `selected: false`
- 平台资质页确认的当前持证主体优先于商标权利人；只有商标证据时必须写明主体角色和剩余风险
- 多源冲突证据优先级：平台资质页 > 信用代码一致 > 商标/品牌官网 > 企业名称相似 > 电话邮箱
- MCP未返回的字段留空
