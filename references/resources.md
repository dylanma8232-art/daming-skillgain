# 资源识别与预校验

评测前自动扫描被评测 skill 的 `SKILL.md`，根据执行依赖上下文**动态识别**所需资源。识别结果附带 `confidence`、`evidence` 和 `detection_reason`，避免把说明性示例或资源类型清单误当成真实执行依赖。

| 类型 | 识别依据 | 是否 blocking | 可自动生成 |
|------|------|:---:|:---:|
| file | 提到 xlsx/csv/json/文件/数据集等 | 视情况 | 是 |
| credential | 提到账号/密码/token/key 等 | 是 | 否 |
| database | 提到数据库/连接/sql 等 | 是 | 否 |
| api_key | 提到 API/endpoint/webhook 等 | 是 | 否 |
| browser | 提到 chrome/playwright/浏览器等 | 是 | 否 |
| environment | 提到 python/依赖/环境/安装等 | 否 | 否 |
| user_confirmation | 提到确认/询问用户/请选择等 | 否 | 否 |
| business_context | 提到业务问题/分析目标/需求等 | 否 | 否 |

注意：资源检测不是固定清单，而是基于 `SKILL.md` 内容动态匹配。只有在资源/输入/前置条件/执行流程等上下文中出现的依赖才作为需要收集的资源；说明性文字、示例和资源类型清单会被降权处理。

如果被测 skill 的 frontmatter 显式声明了 `required_resources`，评测器优先使用该声明；没有声明时才自动识别。
