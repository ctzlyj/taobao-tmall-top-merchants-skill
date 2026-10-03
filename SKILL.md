---
name: taobao-tmall-top-merchants
description: 用于淘宝/天猫类目招商、用户指定商家建联、企业主体补全，或审核已有店铺工作簿的引入画像与优先级。支持店铺名、链接、文本、表格和混合名单；不用于京东SKU识淘宝同款、批量发消息或未授权采集。
---

# 淘宝/天猫top商家清单

## 先选模式，只读相应参考资料

- **浏览器只读模式**：企业查询默认使用网页，先读 `~/.agents/skills/browser-task-router/SKILL.md` 和 `references/browser-enterprise.md`，自主组合工具；优先复用已登录 WebCLI/站点适配器，不为换工具重新登录。不调用付费预检/MCP，不要求双Key；`enterprise_pipeline.py` 默认网页模式，未验证渠道保持待验证。只有用户重新明确授权付费API才启用 `--mode paid-api --allow-paid-api`，以下双Key门槛仅适用于API模式。
- **网页补查来源**：风鸟、企查查、爱企查；优先用户指定且可用的渠道，否则保留已验证路径，缺号码、缺联系人或需交叉核验时再补查。爱企查入口 `https://m.aiqicha.com/`，按 `references/aiqicha-web.md` 执行；受限不等于未披露，不为凑三源重复查询或绕过页面限制。

- **类目发现模式**：只有类目、需要发现优质商家。运行发现与商品样本初筛，详见 `references/workflow.md`。默认目标SPU≥10、样本相关占比≥30%，≥50%为高匹配；淘宝/C店与天猫同一门槛。搜索样本不代表全店主营占比，也不能宣称全市场Top排名。
- **用户指定名单模式**：提供店铺名、多个链接、文本、表格、电子表格或混合信息。由Agent归一化，不要求用户改成固定模板；明确名单优先，未经要求不扩商家。名单店铺全部进入正式招商商家；不运行 `create_job.py`、`mine_taobao.py`、`audit_shops.py`、`audit_storefronts.py`，不为补企业字段打开淘宝。不得补造目标SPU、店内目标商品占比、付款人数展示下限或Top30结论。交付注明“用户指定名单，不代表Top30或主营准入达标”。详见 `references/workflow.md` 的名单分支。
- **审核筛选模式**：已有店铺行，需要填写引入画像与优先级；不得并入用户指定名单模式。运行 `scripts/review_workbook.py`，先 `--dry-run`，再确认负责人/语义列后执行。只处理指定负责人、两项结果均空的行，保留重复源行与空白占位。详见 `references/review-mode.md`。
- **区域走访模式**：用户给区域/产业带和类目，要线下拜访当地公司或工厂。运行 `scripts/local_belt_discovery.py`（collect→parse→verify），企查查检索候选、风鸟核验身份与公示联系方式，零付费API；交付按拜访路线分组的 xlsx 清单。同名企业冲突、虚拟号和单源联系方式必须如实标注，详见 `references/local-belt-visit.md`。

既无类目也无名单时只询问一次；类目歧义才确认商品边界。输入缺字段不等于淘汰，保留原文和来源。

## 就绪门槛

- API模式的类目发现/名单任务必须同时提供并验证企查查 Key 与风鸟 Key，缺一不可；风鸟公共额度不能替代私有 Key，不以其他企业源绕过API模式门槛。经明确付费授权才运行 `scripts/bootstrap.ps1 -AllowPaidApi` 或 `preflight.py --allow-paid-api --install-missing`；名单模式加 `-SkipTaobaoCheck`，API企业查询本身不依赖浏览器。默认预检仅检查本地Python环境，不验证网页登录或企业Key。
- 缺 Key 时指向 `references/mcp-setup.md`，通过 `configure_enterprise_keys.py` 的标准输入/隐藏输入配置。不要让用户自行配置环境变量，不复述密钥。`FN_API_KEY` 等不得进入参数、日志、Git、输出或截图。风鸟底层由 `run_fengniao.py` 注入用户级凭证。
- 审核模式使用 `bootstrap.ps1 -AuditOnly`，不要求企业 Key；浏览器必须就绪。类目发现和审核均需 Browser Bridge 的 `connectivity.ok=true` 及至少一个 `extensionConnected=true` profile，不能只看顶层 `ok=true`。
- 不接管用户正在工作的标签页。复用原浏览器登录态，仅控制任务专属后台页；独立空Profile不继承登录，不能自动要求用户重新登录。若确实必须前台登录/验证才请用户协助。保持站点规定的访问节奏，风鸟详情间隔至少20秒；遇验证码、滑块、隐藏 `_____tmd__` 或登录失效立即保存状态并停止。`MTOP_REQUEST_TIMEOUT` 有界退出，普通瞬时失败最多重试一次，不重试风控。

## 企业查询：执行编排，不只生成计划

统一入口 `scripts/enterprise_pipeline.py`，先 `--job-dir <绝对路径> --limit 1` 只规划，在用户已授权范围内加 `--execute`。网页流程由Agent精确搜索、核验候选并整理 `browser_targets.json`，执行器调用风鸟后台详情适配器，输出逐号码联系人证据及可恢复检查点；缺详情链接/强身份时返回 `needs_browser_discovery`，继续按Router补齐，不静默转API。详见 `references/browser-enterprise.md`。

- 搜索只提供候选，不自动选第一条；公司名与信用代码/工商注册号核验后才归属企业。仅店名不能证明主体。
- 工具按步骤选用：已登录后台优先现有适配器，未知流程可用 Browser-Use，结构/网络诊断与回归可用 Playwright。同一标签页一个控制者，交接URL/业务ID/结果，不交接凭证和元素引用。
- API模式才走双源编排：已有主体为“企查查精确核验 → 风鸟补缺”，仅店名为“风鸟模糊发现 → 企查查精确核验 → 风鸟补缺”，详见 `references/enterprise-workflow.md`。风鸟 `biz_fuzzy_search` 发现内部ID后用 `biz_basic_info` 核验；基础API不支持电话/邮箱补全，网页适配器读取的是页面已展示字段，两者不可混称。
- 直接消费 `platform_qualifications.json`、Agent已确认的 `subjects.json`，或名单模式的归一化 `input_shops.json`；数据结构见 `references/data-contract.md`。不得按第一条搜索结果确认主体。
- 每次合并最新主体证据；成功查询可限时复用，失败不能永久缓存。企业冲突、权限或额度问题暂停批次；重跑只补失败/过期步骤。

## 主体与证据红线

- 证据顺序：平台资质页 > 信用代码一致 > 商标/品牌官网 > 企业名称相似 > 电话邮箱。联系方式不能单独证明店铺主体。
- 平台资质只能来自独立的 platform_qualifications.json；`verified` 必须有公司名、信用代码、资质类型及来源。平台营业执照主体必须与正式表公司名称和信用代码一致。天猫资质来自“查看商家公示信息”的 `liangzhao.htm`；验证码阻断时保持待核验。
- `storefronts.json` 的嵌套字段、企业补全记录的标签不能冒充平台执照；历史数据只能显式迁移或保持待核验。
- 信用代码闭环必须保留来源侧 `matched_credit_code` 与工商侧代码，逐字一致且有来源才可确认。商标/品牌官网证据不得单独写入正式主体。无闭环时 `selected: false`，正式公司、法人、地址和信用代码留空。
- 所有候选及来源保留。正式表以“建联候选公司（非店铺主体，待核验）”及候选联系列展示线索；与平台执照不一致时只能作为非店铺主体线索。
- 不编造未披露字段；保留全部去重电话/邮箱，不截断。优先级结论绑定店铺、类目规则及证据时间，不能跨类目复用。首页样本不足/销量不可观测不是低优先级证据。

## 成品验收与恢复

- 所有模式交付电话时逐号码核验并同时给出联系人/建议称呼、职务、号码归属依据、确认状态及开场话术；法人不等于号码使用人。无对应关系写未确认，来源冲突暂停认定，不把一个姓名套给多个号码。按 `references/browser-enterprise.md` 的联系人决策与固定交付模板执行。

- 类目发现使用 `scripts/build_workbook.py` 生成原六表；生成器仅计算其自身两类固定公式并写缓存，不冒充通用Excel重算引擎。运行 `scripts/verify_job.py` 独立回读商家集合、SPU、公式缓存、主体证据与联系字段；漏行、多行、串店和未计算公式都必须失败。
- 名单模式保留概览、正式商家、主体核验、未确认字段、原始输入、口径说明；不强套类目审计JSON。逐一核对输入商家，不因缺电话、企业候选不唯一或额度不足删行。
- 审核模式只改允许单元格并另存，检查原模板、隐藏表、重复行、空白占位和打印设置；原有两项均非空的结果只按同店同类目复用，仍属于用户提供结论而非本次重新采集证据。
- 交付前检查所有工作表视觉布局、公式结果和敏感字段。只报告本次实测范围；本地测试不能称为线上完成。
- 检查点和JSON原子写入；审核证据默认24小时有效，变更类目/规则或过期重新判定。旧v1检查点备份后迁移，不强行套新规则。
- 源码与安装版更新采用已确认基线的增量同步，保留安装目录独有内容；双方同时修改的文件先合并、回归后替换。不得自动重置、提交或推送。

## 按需资料

- 流程/类目边界：`references/workflow.md`、`references/category-rules.md`
- 企业编排/恢复/回滚：`references/enterprise-workflow.md`
- 爱企查网页补查/受限处理：`references/aiqicha-web.md`
- 数据/主体证据：`references/data-contract.md`
- 审核模式：`references/review-mode.md`、`references/review-data-contract.md`
- 区域走访模式：`references/local-belt-visit.md`
- 首次配置：`references/mcp-setup.md`
