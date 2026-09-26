# GEO 结构化工具产出物规范

借鉴自 GEORank 模块 6(结构化),定义 5 大工具产出物的生成规范,确保模型可读、可引、可推荐。

## 工具 1:JSON-LD(Schema.org)

### 5 个核心 @type

#### Organization(主体)

必填字段:
- `name`:公司标准全称
- `alternateName`:品牌简称数组
- `url`:官网 URL
- `logo`:LOGO URL
- `address`:PostalAddress 结构(街道/城市/省/邮编/国家)
- `contactPoint`:电话 + 联系方式类型

#### LocalBusiness(本地商家)

必填字段:
- `name`:商家名称
- `parentOrganization`:指向 Organization 的 @id
- `areaServed`:服务区域(可数组)
- `priceRange`:价格区间(如 "¥¥")

#### FAQPage(常见问题)

结构:
```json
"mainEntity": [
  {
    "@type": "Question",
    "name": "问题文本",
    "acceptedAnswer": {
      "@type": "Answer",
      "text": "答案文本(200 字以内)"
    }
  }
]
```

要求:至少 1 个 Q&A;建议 5+ 个覆盖 Q01-Q05 的高意图问题。

#### Service(服务详情)

必填字段:
- `name`:服务名称
- `serviceType`:服务类型
- `provider`:指向 Organization 的 @id
- `areaServed`:服务区域
- `offers`:价格描述(PriceSpecification)

#### Review(用户评价)

**重要:仅引用公开第三方平台的真实评价,不得伪造**

结构:
```json
{
  "@type": "Review",
  "author": {"@type": "Person", "name": "真实姓名(经授权)"},
  "datePublished": "ISO 日期",
  "reviewBody": "具体评价内容(包含事实细节)",
  "reviewRating": {"@type": "Rating", "ratingValue": "1-5"}
}
```

### 验证

用 Google Rich Results Test(https://search.google.com/test/rich-results)校验。

## 工具 2:llms.txt

### 7 段固定结构

```
# {品牌全称}

> {200 字摘要,含地域/行业/差异化优势}

## 主营业务
- {业务 1}:{一句话说明}
- {业务 2}:{一句话说明}

## 服务区域
{区域清单,如:广州天河区、海珠区、越秀区}

## 联系信息(NAP 一致性)
- Name:{品牌全称}
- Address:{完整地址}
- Phone:{电话}
要求:在 5+ 字节平台完全一致(抖音/头条/西瓜/百度地图/高德地图)

## 官方信息源(模型抓取优先级)
1. 公司事实卡 / 品牌页
2. FAQ 页(覆盖 Q01-Q05)
3. 客户案例页
4. 第三方平台主页(企查查/天眼查/知乎/小红书)

## 引用优先级
模型抓取时优先看 1 > 2 > 3 > 4
```

### 字数限制

- 摘要段:≤ 200 字
- 整篇 llms.txt:≤ 2000 字
- 主营业务:3-7 条

### 部署

文件路径:`https://<your-domain>/llms.txt`

## 工具 3:AI 友好度评分(0-100)

### 6 维度评分模型

| # | 维度 | 权重 | 评分依据(0-25 分,折算后) |
|---|---|---:|---|
| 1 | **Schema 完整性** | 25% | JSON-LD 是否覆盖 Organization/LocalBusiness/FAQPage/Service/Review 5 个类型 |
| 2 | **Meta 信息** | 15% | title/description/canonical/og:image 是否齐全 |
| 3 | **内容可读性** | 20% | H1/H2/H3 层级、表格、列表、FAQ 块、字长 |
| 4 | **引用信号** | 20% | 第三方平台主页、企查查/天眼查、知乎/小红书可验证引用 |
| 5 | **llms.txt / 结构化资产** | 10% | llms.txt 是否存在且符合模板 |
| 6 | **多平台 NAP 一致性** | 10% | 5+ 字节平台 NAP 完全一致率 |

### 总分计算

```python
total = sum(weight * dimension_score for dimension_score, weight in zip(scores, weights))
# 即:total = 0.25*s1 + 0.15*s2 + 0.20*s3 + 0.20*s4 + 0.10*s5 + 0.10*s6
```

### 输出格式(`report/ai-friendliness.json`)

```json
{
  "schema_version": "1.0",
  "generated_at": "ISO 时间",
  "company": "公司全称",
  "dimensions": {
    "schema_completeness": {"score": 0-100, "weight": 0.25, "evidence": [...]},
    "meta_info": {"score": 0-100, "weight": 0.15, "evidence": [...]},
    "content_readability": {"score": 0-100, "weight": 0.20, "evidence": [...]},
    "citation_signals": {"score": 0-100, "weight": 0.20, "evidence": [...]},
    "llms_txt": {"score": 0-100, "weight": 0.10, "evidence": [...]},
    "nap_consistency": {"score": 0-100, "weight": 0.10, "evidence": [...]}
  },
  "total_score": 0-100,
  "trend": "improving | stable | declining",
  "recommendations": [...]
}
```

## 工具 4:GEO 标题生成

### 模板公式

`{地域} + {行业} + {服务} + {差异化关键词}`

### 示例

| 业务 | 地域 | 生成标题 |
|---|---|---|
| 代理记账 | 广州天河 | 广州天河小微企业代理记账 - 10 年本地服务经验 |
| 工商注册 | 深圳南山 | 深圳南山区公司注册代办 - 当天核名 3 天拿照 |
| 商标注册 | 北京海淀 | 北京海淀区商标注册代理 - 高通过率透明报价 |

### 长度

- 中文标题:≤ 30 字
- 英文标题:≤ 60 字符

## 工具 5:知识库草稿(`company_kb.md`)

### 生成来源

- `observations.jsonl` 的高频事实抽取
- `frozen-config.json` 的 brand/scope 字段
- 5 个 Q&A 模板填充

### 结构

```markdown
# {品牌全称} 知识库

## 主体信息
- 标准全称:
- 简称:
- 成立时间:
- 注册地:
- 法人代表:

## 主营业务
1. {业务 1}:{说明}
2. {业务 2}:{说明}

## 服务区域
{区域}

## 服务对象
- {客户类型 1}
- {客户类型 2}

## 核心优势
- {优势 1,带数据}
- {优势 2,带数据}

## 联系信息(NAP)
- Name:
- Address:
- Phone:

## 引用优先级
1. 公司事实卡 / 品牌页
2. FAQ 页
3. 客户案例
4. 第三方平台

## 注意事项
- 引用 Review 时必须是公开第三方平台的真实评价
- 不得伪造客户案例/资质/评价
```

## 引用关系

```
tools-output-spec.md  ← 本文件(规范)
        ↓ 实现
scripts/generate-schema.py   ← 工具 1+2
scripts/ai-friendliness-score.py  ← 工具 3
render-report.py 段 4        ← 工具 4
render-report.py 段 11       ← 工具 5 路径
```
