# 数据契约

## 两种输入契约

类目发现模式使用下述 `job.json` 与采集审计文件。用户指定名单模式不要求固定文件格式，也不要求创建专用导入脚本；输入可以是店铺名、多个链接、文本、聊天表格、电子表格或混合信息。

名单模式中，Codex在当前任务内归一化以下字段，缺失值留空：

- `category`：用户提供的类目或分组
- `shop_name`：店铺名；未知时保留链接并标记待补
- `platform`：依据明确域名或可靠页面信号识别，否则为“待核验”
- `shop_url`：原始店铺或商品链接，不擅自替换
- `provided_company`、`provided_contact`：用户已给公司与联系方式
- `owner`、`source`：负责人、提供人或来源标记
- `raw_input`：足以回溯原文、原表行或原链接的位置

同一店铺的多条输入合并时保留所有来源URL和冲突值。用户指定名单中的每个唯一店铺都视为正式建联对象，不使用 `passes_minimum`、SPU或占比决定去留。

## job.json

必填：`category`、`queries`、`include_patterns`、`exclude_patterns`。默认参数：`min_spu=10`、`min_share=0.3`、`high_match_share=0.5`、`pages=2`、`interval_seconds=20`、`minimum_payment_lower_bound=10000`、`minimum_quality_query_coverage=3`、`max_candidate_shops=30`。`excluded_shop_patterns` 用于排除综合渠道店；原始候选不等于审计队列。 `sales_top_n_mode` 默认为 `false`；用户要求每类销量Top N时设为 `true`，并设置 `max_candidate_shops=N`、`minimum_payment_lower_bound=0`、`minimum_quality_query_coverage=1`，候选按目标商品付款人数展示下限排序后只审计前N家。

## discovery_raw.json

按查询词保存公开搜索商品：`item_id`、`title`、`shop`、`shop_url`、`user_id`、`sales`、`iconList`、`auctionURL`。

## candidates.json

按类目+店铺聚合：平台、发现目标SPU、查询词、商品样本、店铺链接和用户ID。

## assortment_audit.json

每个店铺记录：`exact_shop_spu_seen`、`target_spu`、`electric_spu`、`accessory_spu`、`unrelated_spu`、`target_share`、`passes_minimum`、`match_grade`、目标商品明细。

搜索反聚合必须标记 `measurement_scope: shop_name_search_sample`、`inventory_complete: false`、`sample_target_share` 和 `full_store_target_share: null`。旧字段 `target_share` 仅兼容样本占比，不能解释为全店主营占比；旧缓存缺少这些字段时也不得推定全店完整。

## storefronts.json

店铺正式URL、`shop_id`、`seller_id`、`shop_type`、页面信号、采集时间。

## platform_qualifications.json

按店铺保存淘宝/天猫平台营业执照原文提取结果。至少包含：`status`、`evidence_type: platform_qualification`、`company_name`、`credit_code`、`legal_person`、`address`、`established`、`source_url`。`status: verified` 要求公司名、信用代码、资质类型与来源同时存在；截图或页面不完整时写 `incomplete`，遇验证码、滑块或 `_____tmd__` 写 `risk_control`。`storefronts.json` 内嵌字段不能替代本文件。

该文件是正式主体的最高优先级锚点。存在 `verified` 记录时，正式表 `company` 和 `credit_code` 必须与其一致；企业搜索、商标或品牌官网返回的其他公司只能作为“建联候选公司（非店铺主体，待核验）”。

## company_candidates.json

企业MCP或风鸟实体识别原始候选。多候选保持原样，禁止覆盖成单一主体。

## subjects.json

由Agent基于证据确认的完整公司名映射：店铺名、公司名、`evidence_type`、主体角色、置信度、证据URL和备注。平台资质不得通过本文件中的标签声明，必须来自独立的 `platform_qualifications.json`。信用代码闭环记录必须使用 `evidence_type: credit_code_match`，并同时保存工商侧统一社会信用代码、来源侧 `matched_credit_code` 和证据来源；代码逐字一致才可设为 `selected: true`。商标/品牌官网、企业名称相似、联系方式或仍存在多个主体时保持 `selected: false`。

## trademark_queries.json / company_trademarks.json

前者按店铺指定 `brand_terms`、`relevant_classes` 和候选 `companies`；后者保存企业MCP有效商标返回及品牌词+类别匹配结果，用于多候选交叉确认。

## company_enrichment.json

确认主体的工商登记与联系方式原始返回。每个候选保留 `selected`、`evidence`、`registration` 和 `contact`；数据来源写入 `evidence` 或单独的 `source` 字段。缺失值保持空白；电话和邮箱全部去重保留，不做条数截断。

双源编排额外保存 `field_sources`、`source_conflicts`、`dual_source_verified`、`fengniao_registration` 和 `captured_at`。`dual_source_verified` 只证明企业数据跨源一致，不确认与店铺的关系。企查查明确返回“已全量扫描该主体联系方式数据库，未发现任何记录。”时，标准化为空电话/邮箱及 `contact_status: not_disclosed`，保留原始返回与商家；不是系统故障。其他未知响应结构必须暂停，不按空数据静默吞掉。

`enterprise_calls.json` 与 `enterprise_checkpoint.json` 保存限时成功缓存、计划指纹、来源与暂停恢复状态，详见企业执行参考。失败或无效数据结构不能写为成功缓存。

## 工作簿

联系人交付记录按“企业+号码”逐项绑定来源、读取时间、姓名/称呼、职务、确认状态及建议开场，遵循 `browser-enterprise.md` 的固定交付模板。原始企业电话和法人字段保持不变，不能为凑联系人而改写源返回或缓存；没有明确对应证据时联系人留空、展示为未确认。原工作簿无联系人列时，在本地交付说明中补齐，不能擅改用户模板或声称旧生成器已自动写入这些字段。

正式表字段至少包括：类目、平台/店铺类型、店铺名、目标SPU、精确店铺SPU、相关占比、匹配等级、付款展示下限、店铺链接、shopId/sellerId、平台营业执照公司名称/信用代码、主体一致性、建联候选公司（非店铺主体，待核验）/电话/邮箱/地址、建联提示、已确认公司名称、法人、电话、邮箱、注册地址、成立日期、信用代码、主体角色/置信度、来源和待确认项。候选建联字段不得被解释为已确认店铺主体。

用户指定名单模式的正式表不要求目标SPU、精确店铺SPU、相关占比、匹配等级和付款展示字段；若保留这些列，必须留空并说明未执行商品结构审计。名单模式至少保留：类目/分组、平台、店铺名、店铺链接、负责人、来源、候选公司/法人/电话/邮箱/地址/成立日期/信用代码/登记状态（均标记待核验）、已确认主体字段、证据、建联提示和待确认项。

名单模式工作簿包含六类信息：

1. 概览：输入店铺数、类目和平台分布、企业候选及联系方式覆盖。
2. 正式招商商家：用户指定的全部唯一店铺。
3. 主体核验：全部企业候选及 `selected`、证据和待确认项。
4. 未确认字段：无候选、额度不足、缺少链接、缺少联系方式和主体未闭环原因。
5. 原始输入：原文、原表行或原链接及归一化结果。
6. 口径说明：明确“用户指定名单，不代表Top30或主营准入达标”，并说明未运行淘宝采集。
