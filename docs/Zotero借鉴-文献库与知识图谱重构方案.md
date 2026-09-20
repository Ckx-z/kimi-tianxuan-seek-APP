# 借鉴 Zotero 重构「科研知识库」——调研报告 + 优化方案

> 任务性质：**调研与方案设计**（未改任何代码）。
> 取证：GitHub/官方文档（附链接） + 本系统源码 + **你机器上的真实数据**
> （`%APPDATA%\COF-Film-Recommend\data`，1711 篇文献库 / 11293 条结构化条目 / 55 张文献图片 / 5 个附件）。
> 待确认项已集中在文末「需要你拍板」。

---

## 0. 先对齐现状：你列的问题里，有些已经修好了

| 你列的问题 | 当前真实状态（已实测） | 结论 |
|---|---|---|
| 图谱选项卡一直显示 0 | **已修（P0，已热更新到你的安装版）**：根因是 `LiteratureFiguresPanel` 的渲染分支漏了 `!fixedPaperId` 判断，固定文献模式（唯一用法）永不加载 `papers` → 画廊分支永远不可达。修后实测 **文献图片（22）+ 22 张缩略图 + 大图查看器** | ✅ 已解决，本方案只需在此基础上**增强** |
| 补解析只能上传一份 PDF、无法上传 SI | **已支持（v1.9.3 起）**：`POST /{paper_id}/parse` 支持 `files[]` 多份（主文 + 多份 SI），按 `role` 分别解析后合并，条目带 `[主文]/[SI n]` 来源标注；附件按 sha1 去重长期留存 | ✅ 已解决；本方案增强为「拖入即识别 + 附件类型扩展」 |
| 补解析后摘要仍显示「暂无摘要」 | **已修（P0）**：根因是回填**必须手动点按钮**，且预览不显示摘要。现解析成功即自动回填（`only_empty=True`），实测 1529 的 `abstract` 0 → 1223 字 | ✅ 已解决（后端随下次安装包生效） |
| LLM 结构化提取失败 | **已修（v1.9.3）**：推理模型 token 预算被思考过程吃光 → 默认 16000 并自动扩容至 32000；表征白名单大小写 bug 修复（拒收率 42% → 0.5%）。实测同篇产出 211 条 | ✅ 已解决；本方案做 Prompt 结构化增强 |
| 文献编号从 1782 递增 | **已实现**：`resolver.next_paper_id()` = 库内 `max(id)+1`，内置库到 1781 → 新文献自动 1782 起 | ✅ 已解决 |
| 文献录入未接入知识图谱 | **部分实现**：录入即写 `LITP-<paper_id>` 文献节点（v1.9.3），条目入组时写 `reaction` 节点与 `reaction_cited_in` 边；但**缺文献↔文献关系**（引用/相似）与实体级节点 | ⚠️ 需扩展（本方案 §5） |

**所以本次真正的设计重点是「结构性升级」**：数据模型（§2）、图谱联动（§5）、LLM/视觉集成（§6），
以及把你已经确认的交互决策（报告可挑选片段、方案卡经助手讨论生成等）落到架构里。

---

## 1. Zotero 调研摘要

### 1.1 核心数据模型（`zotero.sqlite`，SQLite）

官方明确：**所有库数据都在 `zotero.sqlite`**；外部访问**只应只读**（运行中修改会因缓存层破坏文件锁导致库损坏），
且**表结构会随版本变化**——所以正解是「只读借鉴其**模型思想**」，而不是耦合它的表。
每条书目记录 = 「item type（journalArticle/book/…）+ 一组 fields（title/date/DOI…）+ 字段值」，
item types 与 fields 的权威定义在 [`zotero/zotero-schema`](https://github.com/zotero/zotero-schema/tree/master)；
**每个 item 还各自带**：一组 notes、一组 attachments、一组 tags、一组「相关条目」链接。
附件不是塞进数据库，而是**存在数据目录中、以父 item 的 key 命名的子目录里**。

表结构（**一手来源**：[`resource/schema/userdata.sql`](https://github.com/zotero/zotero/blob/main/resource/schema/userdata.sql)
与 [`system.sql`](https://github.com/zotero/zotero/blob/main/resource/schema/system.sql)，非猜测）：

| 表 | 职责 | 关键字段 | 关系 |
|---|---|---|---|
| `items` | **一切对象**（书目/附件/笔记/批注）公共属性 | `itemID` PK, `itemTypeID`, `libraryID`, `key`, `dateAdded`, `dateModified`, `version`, `clientVersion`, `synced` | UNIQUE(libraryID,key)；FK libraryID→libraries CASCADE |
| `itemTypes` / `fields` / `itemTypeFields` / `baseFieldMappings` | 类型字典 / 字段字典 / 「某类型有哪些字段+顺序+默认隐藏」/ 类型特有字段↔基础字段映射 | `typeName` / `fieldName` / (`itemTypeID`,`fieldID`,`hide`,`orderIndex`) / (`itemTypeID`,`baseFieldID`,`fieldID`) | 定义来自 [`zotero/zotero-schema`](https://github.com/zotero/zotero-schema/tree/master) |
| `creatorTypes` / `itemTypeCreatorTypes` / `creators` / `itemCreators` | 角色字典 / 类型↔角色合法性 / 责任者实体（去重）/ 条目↔责任者（**含 `orderIndex` 与角色**） | `first/lastName`, `fieldMode` | `itemCreators` PK(itemID,creatorID,creatorTypeID,orderIndex)，UNIQUE(itemID,orderIndex) |
| `itemData` + `itemDataValues` + `fields` | **EAV 三件套**：事实表 / 值池（值去重） / 字段字典 | (`itemID`,`fieldID`,`valueID`) / (`valueID`,`value` UNIQUE) | 见下 §1.2 |
| `itemAttachments` | 附件专属元数据 | `itemID` PK, **`parentItemID`**, **`linkMode`**, `contentType`, `path`, `syncState`, `storageHash` | FK parentItemID→items（**NULL=独立附件**） |
| `itemNotes` | 笔记专属数据 | `itemID` PK, `parentItemID`, **`note`（HTML 字符串）**, `title` | NULL=独立笔记；非 NULL=条目子笔记 |
| `itemAnnotations` | **PDF 批注**（挂在附件下） | `itemID` PK, `parentItemID`, `type`, `text`, `comment`, `color`, `pageLabel`, `position` | FK parentItemID→**itemAttachments(itemID)** |
| `collections` + `collectionItems` | 集合（`parentCollectionID` **自引用→任意深度嵌套**）+ 多对多 + 手动排序 | `collectionName`, `parentCollectionID`, `key` / (`collectionID`,`itemID`,`orderIndex`) | 全 FK CASCADE |
| `tags` + `itemTags` | 库级标签字典（`name` UNIQUE）+ 关联 | (`itemID`,`tagID`,**`type`**) | **`type` 0=手动 / 1=自动**（自动标签可整类清除） |
| `itemRelations` + `relationPredicates` | 通用「条目↔任意 URI」关系 | (`itemID`,`predicateID`,`object`) / `predicate`（如 `owl:sameAs` / `dc:relation` / `dc:replaces`） | 开放关系，不限于文献间 |
| `libraries` / `groups` / `groupItems` | 个人库/群组库/群组成员 | `type`,`editable`,`version`,`storageVersion`,`lastSync` | — |
| `deletedItems` / `deletedCollections` / `deletedSearches` | **回收站（软删除，独立表）** | ID PK + `dateDeleted` | 条目本身仍在 `items`；API：`/items/trash`、`includeTrashed=1` |
| `fulltextItems` | 全文索引状态 | `indexedPages`,`totalPages`,`indexedChars`,`totalChars`,`version` | 词级表（`fulltextWords` 等）**未确认**（另一 schema 文件） |
| `syncedSettings` / `settings` | 库级可同步设置（**标签颜色存这里**，见 §1.5）/ 本机设置 | `setting`,`value`,`version` | — |

补充：`key` 是 **8 字符库内唯一串**（规则 `/[23456789ABCDEFGHIJKLMNPQRSTUVWXYZ]{8}/`），
附件实体放在 `<data dir>/storage/<itemKey>/`（**不是** DB 里）。

### 1.2 关键设计点（最值得借鉴的 5 条）

1. **统一 item 表 + 子类型表**：书目/附件/笔记都是 `items` 里的一行，各自在 `itemAttachments`/`itemNotes` 里有扩展行，
   用 `parentItemID` 表达父子。→ **一个「文献」天然可以挂 N 个附件与 N 条笔记**，且关系查询极简。
   （对照我们：文献在 JSON dict、附件在 jsonl、图片在另一个 json、条目在另一个 jsonl，四者只靠 `paper_id` 字符串松散关联。）
2. **附件文件按父条目 key 分目录存放**：文件系统承载大文件，DB 只存索引/路径。
   （我们已经这样做了：`literature/pdfs/<paper_id>/<file_id>_<name>.pdf` ✓ 方向一致。）
3. **EAV 元数据（`fields`+`itemData`+`itemDataValues`）**：让「不同 item type 有不同字段」而**不改 DDL**
   （`itemTypeFields` 决定某类型有哪些字段/顺序/默认隐藏，插件还能加 customFields），`itemDataValues` 把重复值去重。
   **代价**：取一个字段要 `items ⋈ itemData ⋈ itemDataValues ⋈ fields` 四表 JOIN；值统一为 TEXT，
   **排序/范围过滤拿不到类型化索引**；索引只有 `itemData(itemID,fieldID)` 主键与 `fieldID`/`valueID` 单列。
   （诚实标注：官方文档**只说明**字段集取决于 item type、定义在 zotero-schema，**没有解释**为何用 EAV——
   上面的动机是调研推断。）
   → **给我们的结论**：我们不做「开放自定义类型/字段」，**宽表 + JSON 列**远比 EAV 查询友好；
   但必须保留 Zotero 的三样东西：**`key`（稳定外部标识，Zotero 用 8 字符串）**、
   **`version`（乐观并发/增量同步）**、**软删除表**。
4. **集合（树）+ 标签（多对多）双轨组织**：集合=人工目录（`parentCollectionID` 自引用、**任意深度**、
   条目可多归属、集合内可手动排序），标签=跨集合的横切属性（`tags` 是库级字典 + `itemTags.type` 区分
   **0=手动 / 1=自动**，自动标签可整类清除；**标签颜色不存在 tags 表，而存在库级 `syncedSettings` 的
   `tagColors`，彩色上限 `MAX_COLORED_TAGS=9`**）。（我们目前两者都没有。）
5. **通用 `itemRelations(itemID, predicateID, object)` + `relationPredicates` 字典**：谓词是 URI 形式
   （`owl:sameAs` / `dc:relation` / `dc:replaces`），为「相关/引用/替代」这类关系留了统一扩展位，
   Zotero 的「Related」面板就是它的 UI。（这正是我们图谱缺的「文献↔文献」层。）
6. **附件 linkMode 有 5 种**（源码常量）：`0` imported_file（**存副本**，纳入同步，删条目即删文件）、
   `1` imported_url（网页快照）、`2` linked_file（**只记路径**，不同步、不随条目删）、`3` linked_url（只记 URI）、
   `4` embedded_image（笔记内嵌图）；path 用前缀区分来源：`storage:<filename>`（实体在 `<data dir>/storage/<itemKey>/`）
   与 `attachments:<相对路径>`（在 Linked Attachment Base Directory 下时）。
7. **「主文 + SI」在 Zotero 没有专属类型**：官方论坛答复是「SI 应挂到**同一父条目**下」，
   即 1 个书目 item + N 个子附件，靠文件名/title 区分。
   → **我们的 `role=main|si` 显式建模比它更清楚**，保留即可（并可扩展 `dataset/code`）。
8. **笔记是 HTML 而非 Markdown**（`itemNotes.note` 存 HTML 字符串、含 `data-schema-version`），
   且笔记本身就是 item（`itemTypeID=note`）、可作条目子笔记或独立笔记。
   → 我们面向 LLM 与渲染，**建议存 Markdown**（`notes.content_md`），导出/互操作时再转 HTML。
9. **软删除是独立表**（`deletedItems`/`deletedCollections`/`deletedSearches` 只存 ID + `dateDeleted`），
   配合 API 的 `/items/trash` 与 `includeTrashed=1`。（我们用 `deleted_at` 列即可，但语义照抄。）

### 1.3 本地 API（Zotero 7+，`http://localhost:23119/api/`）

官方文档页（此前我按旧路径 404，正确地址）：[Local API](https://www.zotero.org/support/dev/web_api/v3/local_api)。

- **开启与认证**：必须在 Zotero **Settings → Advanced → “Allow other applications on this computer to
  communicate with Zotero”** 勾选，否则一律 `403`；地址固定 23119（不可配），仅实现 **API v3**。
  **读请求免认证、无限速、默认不分页**（省略 `limit` 即全量返回）；写请求（Zotero 10+）需先
  `POST /api/local/authorize`（带 `Zotero-Server-ID`）弹窗取本地 key，与 zotero.org 的 key 无关。
- **与 Web API 的一致性**：**同一套 v3 schema、同一 JSON 形状**（`key/version/library/links/meta/data`），
  官方称 Basics / Write / File Uploads / Full-Text 各端点「work identically under that prefix」。
  差异：无 Atom（`format=atom` → 501）、type/field 名本地化、默认不分页、无 Backoff 限速、
  Zotero 10+ 的 version 是**本地版本**且响应带 `Zotero-Server-ID` / `Zotero-Schema-Version`、
  群组元数据精简只读、不支持分块 PATCH 上传（405）。
- **端点清单**（`/users/0/...` 即「本地用户库」）：

| 端点 | 语义 | 关键参数 |
|---|---|---|
| `GET /api/` | 探测运行中的客户端与版本 | 响应头 `Zotero-API-Version`（本地仅 v3） |
| `/users/0/items` | 全部条目（不含回收站，**含子条目**） | `format(json/bibtex/biblatex/csljson/ris/keys/versions)`, `include(data,bib,citation)`, `limit`, `start`, `sort`, `direction`, `itemKey`, `itemType`, `q`, `qmode`, `tag`, `since`, `includeTrashed` |
| `/users/0/items/top`、`/items/trash` | 仅顶层条目 / 回收站 | 同上 |
| `/users/0/items/{key}` | 单个条目 | `format`, `include` |
| `/users/0/items/{key}/children` | **子条目 = 附件 + 笔记 + 批注** | `limit/start/sort` |
| `/users/0/items/{key}/fulltext` | 附件全文（`content` + `indexedPages/totalPages`） | 404 = 无全文 |
| `/users/0/fulltext?since=` | 全库全文变更清单 | `since` 必填 |
| `/items/{key}/file`、`/file/view`、`/file/view/url` | **本地独有**：302 跳 `file://` 本地文件 / 返回纯文本 URL | — |
| `/users/0/collections`、`/collections/top`、`/collections/{key}/collections`、`/collections/{key}/items` | 集合与子集合、集合内条目 | `limit/start/sort/since` |
| `/users/0/items/tags`、`/tags`、`/tags/{name}`、`/items/{key}/tags`、`/collections/{key}/tags` | 标签（响应含 `meta.type` 与 `meta.numItems`） | `q`, `qmode(contains/startsWith)`, `tag`, `since` |
| `/users/0/searches`、`/searches/{key}/items` | 保存的检索；**后者为本地独有**：真正执行检索 | 同 items |
| `/itemTypes`、`/itemFields`、`/itemTypeFields?itemType=`、`/itemTypeCreatorTypes`、`/creatorFields`、`/items/new?itemType=` | schema 字典与新建模板 | `itemType`（`locale` 被忽略） |

- **对我们的借鉴价值**：① `/items/{key}/children` 的**子资源设计**（附件+笔记+批注一次拿全）
  正是我们「按 `paper_id` 分散在 4 个文件」缺的；② `limit/start/q/qmode/tag/since/sort/direction`
  这套**服务端检索参数**直接补我们 `/papers` 全量返回、前端本地过滤的短板；
  ③ `format=bibtex|csljson|ris` 说明**导出格式应内建**；④ `/items/{key}/file` 302 到本地文件的做法，
  比我们返回文件流更省内存（大 PDF 友好）；⑤ `fulltext` + `fulltext?since=` 提示全文检索与**增量**是一等公民
  （我们可直接用 SQLite FTS5 + `version`）。
- **无官方 OpenAPI/Swagger**（未找到）；官方端点的权威定义在
  `zotero/zotero` 的 `resource/schema/*.sql` 与 `chrome/content/zotero/xpcom/server/server_localAPI.js`。
  可复用的类型资产：[windingwind/zotero-types](https://github.com/windingwind/zotero-types)（插件内 `Zotero.*` JS API，
  **不覆盖 HTTP JSON 形状**）、[dzackgarza/zotero-local-write-api 的 openapi.yaml](https://github.com/dzackgarza/zotero-local-write-api/blob/main/openapi.yaml)
  （真 OpenAPI 3.1，但描述的是该第三方 add-on 自建端点，**非官方**）。

### 1.4 知识图谱类插件（**仓库地址与活跃度已逐个核实**，含同名混淆澄清）

| 项目 | 定位（按仓库自述） | 关键机制（可借鉴） | 活跃度/许可 |
|---|---|---|---|
| [`alisoroushmd/zotero-mcp`](https://github.com/alisoroushmd/zotero-mcp) | MCP server：搜索/建条目/Word 实时引文/**引文图谱与知识图谱** | ① **「两工具契约」抽实体**：server 只提供 `get_unextracted_abstracts` 与 `store_entities`，**抽取由调用方 LLM 做** → 模型供应商彻底解耦；② 图**先落库**（SQLite+NetworkX 持久化），再按 `MATERIALIZATION_LIMIT`（默认 10 万条）**物化进内存，超限直接拒绝**而非 OOM；③ 用 MCP **Prompts 把多工具流程固化成可复用编排** | **仅 3★**、MIT、push 2026-09-17 |
| ⚠️ [`54yyyu/zotero-mcp`](https://github.com/54yyyu/zotero-mcp) | **同名但不同项目**：主流实现（5090★/405 fork），只做**语义检索+标注**，**不做知识图谱** | —— | 5090★、MIT |
| [`amberbiology/scribl`](https://github.com/amberbiology/scribl) | 生物文献**关系语义捕获**：用**标签即图数据**写进 Zotero，再解析成图库 | ① **人类可读 DSL 存在 Zotero 标签里**——Zotero 自身即编辑器与版本管理，避免外挂库不同步；② **一份图模型多后端导出**（Cypher / GraphML / NetworkX），存储与查询解耦；③ 节点强制带**外部权威 ID**（如 UniProt URL）→ 为实体对齐留钩子 | 5★、**AGPL-3.0**、有 JOSS 论文 |
| [`yimmy23/zotero-refs`](https://github.com/yimmy23/zotero-refs) | 每个条目四栏：References / Cited By / Related / **Citation Graph** | ① **「本地 PDF 文本层定骨架 + 在线 API 补字段」的融合**，任一侧缺失另一侧仍可用，且**逐字段标注来源**（DOI 覆盖率 699 篇语料 19%→80%）；② **边按可信度分层渲染**：实线=确证引用、虚线=provider 推荐（明确标注非真实引用）、细线=共享参考文献（bibliographic coupling）；③ 节点视觉编码（大小=被引量、实心=已入库）；④ 不确定结果**强制人工确认**（撤稿标记、行编辑保留原始引文证据） | 12★、**AGPL-3.0-or-later**、2026-09 push |
| [`Stef4678/citation-atlas`](https://github.com/Stef4678/citation-atlas) | 把 Zotero items/collections/authors 导出为 Obsidian **`.canvas`（JSON Canvas 1.0）**引用地图 | ① **不发明格式，落到成熟宿主格式**（Obsidian 原生可编辑）→ 免费获得拖拽/聚类/涂色；② **「外部被引作品只做一层、默认上限 60」**的防爆炸策略；③ **从真实信号推导阅读状态**（标注子条目→annotated；`Read` 前缀→read），不新增私有字段 | 0★（极新）、**MIT** |

> 说明：`zotero-refs` 与 `citation-atlas` **都不用 LLM**——它们的"智能"是确定性算法（多源融合 + RRF + 结构推断），
> 这点对我们有启发：**引用/相似关系应尽量用确定性来源，LLM 只做兜底抽取**。

### 1.5 LLM 集成类插件

| 项目 | 定位 | 关键机制（可借鉴） | 活跃度/许可 |
|---|---|---|---|
| [`steven-jianhao-li/zotero-AI-Butler`](https://github.com/steven-jianhao-li/zotero-AI-Butler) | LLM **自动精读**论文库 → 写回 **Zotero 原生笔记** | ① **生产者-消费者任务队列 + 限速（每分钟处理数）+ 可观测任务面板**（pending/running/done/failed）——这是 LLM 批处理的**必备件而非可选**；② **PDF 以 Base64 直送多模态模型**（公式/图表/复杂排版无损，纯图 PDF 也能读），可切文本模式；③ 三种触发（右键即时/巡航扫新/批量扫未总结）；④ **提示词模板可自定义 + 变量实时预览**（`{{title}}`/`{{authors}}`）；⑤ **多轮「拼接 vs 总结」两种聚合策略并显式标注 token 消耗** | **1744★/97 fork**（本批最活跃）、**AGPL-3.0** |
| [`congcongwang0122/zotero-skill`](https://github.com/congcongwang0122/zotero-skill) | 连 Zotero 本地 API 的「整理分类 + 生成综述」skill | ① **skill 式渐进披露**（frontmatter 只写触发条件，正文命中才载入）比 MCP 全量 schema 省 context；② **直接 HTTP 调用作为 helper 脚本的 fallback**（去掉单点依赖）；③ **只读默认 + 写操作先确认**；④ 输出契约强制「可追溯字段 + 显式 blocker 命名」（Zotero 未开/本地 API 未启用/PDF 缺失） | 193★、**无 LICENSE（不可直接抄代码）** |
| [lit-monitor（PyPI 0.11.0）](https://pypi.org/project/lit-monitor/) | 语义文献发现：按**与你自己的 Zotero 库的相似度**排序新论文 → LLM 抽结构化字段 | ① **以用户自己的库作为相关性先验**（库本身就是兴趣模型，省掉领域配置）；② **Markdown 里的 persist zone**：机器生成内容可反复重建，而**人工批注永不被覆盖**；③ `--rag-mode {vector,graph,hybrid}` 三态可切 + 显式 RRF 融合 | Beta、MIT、**不支持 Windows（硬伤）**、GitHub 仓库当前 404（**未确认**） |

> ⚠️ **前车之鉴**：lit-monitor 深度依赖 **`kuzu>=0.6`**，而 KuzuDB 仓库**已被 GitHub 标记 `archived`**
> （最后 push 2025-10-10，329 个 open issue 无人处理）——**图数据库选型必须避开它**（详见 §5.3）。

### 1.6 插件模板（数字为调研时刻的 GitHub 元数据）

- [`windingwind/zotero-plugin-template`](https://github.com/windingwind/zotero-plugin-template)：
  **AGPL-3.0**，**846★ / 178 fork**，`pushed_at = 2026-03-09`（仍活跃，open issues 6）。
  提供：全量 TS 类型（依赖 zotero-types）；事件驱动骨架（`bootstrap.js` → `src/hooks.ts` 的 onStartup/onShutdown）；
  丰富示例（通知、右键菜单、额外列、侧栏/Reader 面板、prefs 面板、快捷键）；**自动热重载**
  （`npm start` 拉起开发版 Zotero，改 `src`/`addon` 即重载）；构建/发布流水线（zotero-plugin-scaffold + esbuild，
  `npm run build` 出 `.xpi`，`npm run release` 走 GitHub Actions 发 Release 与 `update.json`）。
  配套 [`zotero-plugin-toolkit`](https://github.com/windingwind/zotero-plugin-toolkit)（**MIT**，199★，2026-07-21）
  与 [`zotero-types`](https://github.com/windingwind/zotero-types)（85★，2026-08-01）。
  → 若未来要让本系统与 Zotero 双向互通（读它的库 / 被它调用），可基于它做插件；**本期不做**，但架构上留出 HTTP 互操作口。

### 1.7 可借鉴的关键设计点（汇总）

**A. 数据模型层**（来自 Zotero 本体，§1.1/§1.2 已展开）
1. **统一 item + children 模型**（文献/附件/笔记/图片同构，父子用 `parentItemID`）。
2. **集合（树）+ 标签（多对多）**双轨组织，条目可多归属。
3. **服务端检索参数 + 全文索引（FTS）**，而不是把全量数据丢给前端过滤。
4. **通用 relations 表**承载「引用/相关/衍生」等开放关系 → 直接对接知识图谱。
5. **精读产出→笔记**，笔记与条目强绑定（LLM 产出有人工确认位）。
6. **附件走文件系统、DB 只存索引**；附件按父条目分目录。
7. **只读借鉴 DB 结构**（官方警告：直接改库会坏）→ 我们对外**只提供 API 互操作**，不碰别人的库文件。

**B. 工程机制层**（来自插件调研，最实用的 11 条）
8. **「两工具契约」抽实体**（zotero-mcp）：服务端只提供「取未抽取文本」与「写回类型化实体」，
   **抽取由调用方 LLM 决定** → 模型供应商与存储层解耦；我们换云/换本地不动数据层。
9. **图先落库、按上限物化进内存，超限拒绝而非 OOM**（zotero-mcp，默认 10 万条）→ 直接用于我们的 NetworkX 视图。
10. **边按可信度分层**（zotero-refs）：实线=确证引用（DOI/Crossref 匹配）、虚线=算法推荐（**明确标注非真实引用**）、
    细线=共享参考文献（bibliographic coupling）→ 我们建 `cites`/`similar_to` 必须带 `source` 与置信度，**不做无差别合并**。
11. **多源融合 + 逐字段来源标注**（zotero-refs）：本地 PDF 文本层定骨架 + 在线 API 补字段，
    任一侧缺失另一侧仍可用（DOI 覆盖率 19%→80%）→ 我们的「摘要三级兜底」「作者/期刊回填」照此设计。
12. **LLM 批处理的必备件：队列 + 限速 + 可观测任务面板**（AI-Butler，pending/running/done/failed）→ 不是可选项；
    我们做「精读生成笔记」「批量视觉标注」前必须先有队列与进度。
13. **多轮「拼接 vs 总结」两种聚合策略并显式标注 token 成本**（AI-Butler）→ 用于分段解析与报告生成。
14. **PDF 以 Base64 直送多模态模型**（AI-Butler）→ 扫描件/复杂排版的兜底路径（比自建 OCR 管线简单）。
15. **Markdown persist zone**（lit-monitor）：机器生成内容可反复重建，**人工批注永不被覆盖** →
    直接用于我们的「笔记」与「导出」（LLM 段落放标记区内，重跑只更新区内）。
16. **输出落到成熟宿主格式**（citation-atlas 的 JSON Canvas、zotero-mcp 的 D3 HTML）+ **防爆炸参数**
    （外部被引只做一层、默认上限）→ 我们导出图谱时不发明格式。
17. **渐进披露 + 只读默认 + 显式 blocker**（zotero-skill）：工具描述分层加载；写操作先确认；
    失败必须点名具体关卡（服务未开/端口关闭/无匹配），不返回含糊错误。
18. **以用户自己的库作为相关性先验**（lit-monitor）：库本身就是兴趣模型，无需领域配置 →
    可做「新文献按与你库的相似度排序」推荐（复用我们已有的 `embedding.py`）。

> **许可提示**：`scribl` / `zotero-refs` / `zotero-AI-Butler` 为 **AGPL-3.0**（传染性），
> `citation-atlas` 为 MIT，`zotero-skill` **无 LICENSE**（默认保留所有权利）。
> **我们只借鉴设计思想、不复制代码**；若将来要复用代码，只取 MIT/BSD 类。

---

## 2. 文献库数据模型重构方案

### 2.1 现状（实测，必须如实说明）

| 数据 | 现存储 | 问题 |
|---|---|---|
| 文献条目 | **单个 JSON dict**：内置库 `_internal/data/paper_titles.json`（1781 篇，随包）+ 用户新增**直接追加写进安装目录**；另有 `literature/literature_intake.jsonl` 审计流水 | ① 写进安装目录 → 升级安装可能被覆盖；② 无事务/并发保护（仅 `threading.Lock` + 原子替换）；③ 字段无约束；④ **无集合/标签/笔记**；⑤ `/papers` 全量返回（1711 条、只 5 个字段）、**无服务端检索/分页/排序** |
| 附件 | `literature/pdfs_index.jsonl`（5 条）+ 文件 `literature/pdfs/<paper_id>/`（`role=main/si`, `sha1`, `pages`, `chars`） | 结构合理；缺「附件类型」扩展（数据集/代码/图）、缺外键完整性校验 |
| 结构化条目 | `literature/knowledge_entries.jsonl`（**11293 条**，7 种 `kind`：monomer_pair 6197 / condition 2489 / film_outcome 2380 / characterization 104 / property 87 / conclusion 27 / dft 9） | 全量重写式写入；无索引；数值指标埋在 `metrics` 数组里**无法跨文献做数值检索** |
| 文献图片 | `literature/figures_index.json`（**55 条**：spectra 44 / mechanism 9 / structure 2） | 已能展示（P0 修复）；缺集合/标签归属与视觉标注来源标记 |
| 笔记 | **不存在** | 借鉴 Zotero 补上（LLM 精读/助手结论落笔记） |
| 集合/标签 | **不存在**（只有单体「收藏夹」，与文献无关） | 借鉴 Zotero 补上 |
| 知识图谱 | NetworkX 侧车图（`user_graph`）+ 随包图（5713 文献/6197 反应）：节点 `literature/reaction/monomer/solvent/catalyst/outcome`，边 `reaction_cited_in/uses_*/produces` | 碎片化（无文献↔文献关系、无实体级节点、无向量相似边） |

### 2.2 目标模型（SQLite，单文件 `library.db`）

**为什么是 SQLite**：单机桌面、零运维、stdlib `sqlite3`（PyInstaller 友好）、事务与外键、索引、
**FTS5 全文检索**（Zotero 同款思路），且可平滑放到 `%APPDATA%` 用户目录（解决「写安装目录」隐患）。
不使用 Zotero 的 EAV：COF 域字段稳定，宽表 + `extra_json` 更简单（官方也建议自定义信息进 Extra）。

```sql
-- 文献条目（书目）
items(
  id INTEGER PRIMARY KEY,           -- 内部自增
  key TEXT UNIQUE,                  -- 稳定外部标识（借鉴 Zotero 8 字符串 key，供导出/互操作/引用）
  paper_id TEXT UNIQUE NOT NULL,    -- 兼容现有 paper_id（1782 起递增，保持对外不变）
  item_type TEXT DEFAULT 'journalArticle',
  title TEXT, abstract TEXT, journal TEXT, year INTEGER, doi TEXT UNIQUE, url TEXT,
  authors_json TEXT,                -- [{first,last,order}]（顺序敏感，借鉴 itemCreators.orderIndex）
  source TEXT,                      -- builtin | crossref | user-intake | zotero-import
  extra_json TEXT,                  -- 扩展字段（Zotero「Extra」思路）
  added_at TEXT, updated_at TEXT,
  deleted_at TEXT,                  -- 软删除（借鉴 deletedItems，但用列而非独立表）
  version INTEGER DEFAULT 1         -- 乐观并发/增量（借鉴 Zotero version）
);

-- 附件（主文/SI/图/数据/代码）—— 借鉴 itemAttachments
attachments(
  id INTEGER PRIMARY KEY, file_id TEXT UNIQUE, item_id INTEGER REFERENCES items(id),
  role TEXT,                        -- main | si | figure | dataset | code | other
  filename TEXT, path TEXT, sha1 TEXT, size INTEGER, pages INTEGER, chars INTEGER,
  mime TEXT, link_mode TEXT DEFAULT 'imported_file',
  caption TEXT, page INTEGER, created_at TEXT
);

-- 文献图片（图谱）—— 与附件解耦：既可是附件抽出的，也可是截图/生成图
figures(
  id INTEGER PRIMARY KEY, fig_id TEXT UNIQUE, item_id INTEGER REFERENCES items(id),
  attachment_id INTEGER REFERENCES attachments(id),   -- 来源附件（可空）
  figure_type TEXT, caption TEXT, caption_source TEXT, -- pdf | vision | user | none
  tags_json TEXT, meta_json TEXT, file TEXT, mime TEXT, size INTEGER,
  page INTEGER, score_note TEXT, created_at TEXT, updated_at TEXT
);

-- 结构化条目（LLM 提取结果）
entries(
  id INTEGER PRIMARY KEY, entry_id TEXT UNIQUE, item_id INTEGER REFERENCES items(id),
  group_id TEXT, kind TEXT, experiment TEXT, evidence TEXT,
  source TEXT, status TEXT, graph_indexed INTEGER DEFAULT 0,
  payload_json TEXT, created_at TEXT, updated_at TEXT
);
entry_metrics(                      -- 数值指标规范化（跨文献数值检索的关键）
  id INTEGER PRIMARY KEY, entry_id TEXT REFERENCES entries(entry_id),
  name TEXT, value REAL, unit TEXT, technique TEXT, sample TEXT
);

-- 笔记（借鉴 Zotero itemNotes）—— LLM 精读/助手结论/人工笔记统一落这里
notes(
  id INTEGER PRIMARY KEY, note_id TEXT UNIQUE, item_id INTEGER REFERENCES items(id),
  parent_note_id INTEGER,           -- 支持笔记嵌套（Zotero 同款）
  title TEXT, content_md TEXT,      -- 我们存 Markdown（Zotero 存 HTML；导出时再转）
  source TEXT,                      -- user | assistant | llm
  created_at TEXT, updated_at TEXT
);

-- 集合（树）+ 多对多挂载（借鉴 collections/collectionItems）
collections(id INTEGER PRIMARY KEY, collection_id TEXT UNIQUE, name TEXT,
            parent_id INTEGER, order_index INTEGER, created_at TEXT, deleted_at TEXT);
collection_items(collection_id INTEGER, item_id INTEGER, order_index INTEGER,
                 PRIMARY KEY(collection_id, item_id));

-- 标签（多对多；type 区分自动/手动，借鉴 tags/itemTags；颜色存 settings 而非标签表）
tags(id INTEGER PRIMARY KEY, name TEXT UNIQUE, type TEXT DEFAULT 'manual');  -- manual | auto
item_tags(item_id INTEGER, tag_id INTEGER, PRIMARY KEY(item_id, tag_id));

-- 库级设置（借鉴 syncedSettings：标签颜色等放这里，而非污染 tags 表）
settings(key TEXT PRIMARY KEY, value_json TEXT, updated_at TEXT);

-- 通用关系（借鉴 itemRelations）—— 文献↔文献、条目↔图谱节点
relations(id INTEGER PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT,
          meta_json TEXT, created_at TEXT);

-- 图谱（DB 内的可查询镜像；侧车图仍保留为算法视图）
graph_nodes(node_id TEXT PRIMARY KEY, node_type TEXT, name TEXT, meta_json TEXT);
graph_edges(subject TEXT, predicate TEXT, object TEXT,
            meta_json TEXT, PRIMARY KEY(subject, predicate, object));

-- 全文检索（借鉴 Zotero FTS）
CREATE VIRTUAL TABLE items_fts USING fts5(
  paper_id UNINDEXED, title, abstract, fulltext, authors, tags, content='');
```

**关系图（一句话）**：`items` 是中心；`attachments/figures/entries/notes` 都是它的「子资源」；
`collections/tags` 是与 `items` 的多对多；`relations/graph_*` 承载跨条目与图谱关系。

### 2.3 多附件关联（主文 + 多份 SI）

- **模型层**：一条 `items` 记录 → N 条 `attachments`，`role ∈ {main, si, figure, dataset, code}`；
  **允许多个 `main`**（例如正文 + 勘误），UI 默认折叠 SI。
- **落盘层**：`data/literature/pdfs/<paper_id>/<file_id>_<name>.pdf`（保持现状，不迁移文件）；
- **解析层**：`POST /{paper_id}/parse` 已支持多文件 → 只需把「角色判定」从「第 1 个是主文」升级为
  **文件名/内容启发式**（`SI|supp|mmc|supporting` → si）+ **UI 拖拽时手动改角色**；
- **完整性**：`attachments.item_id` 外键 + `sha1` 唯一去重（已有）+ 删除文献时级联（软删 `deleted_at`）。

### 2.4 图谱数据存储方案

- **DB 内**：`figures`（图片本身，文件在 `literature/figures/`）+ `graph_nodes/graph_edges`（可 SQL 查询的图镜像）
  + `relations`（开放关系）；
- **算法侧**：保留 NetworkX 侧车图（`user_graph`）作为「合并视图」——因为随包图（5713 文献/6197 反应）是
  只读大图，把它整体导入 DB 收益低；采取**「内置图只读 + 用户图入 DB」**的混合策略，
  查询时用 `graph_edges` 与侧车图做并集（现有 `query_graphrag` 已支持合并，改造点小）。
- **向量**：现有 `literature/embedding.py`（本地 bge-m3 或在线 text-embedding-v4）已能对**条目**做向量检索 →
  新增 `entry_vectors(entry_id, vector_blob)` 与 `paper_vectors(paper_id, vector_blob)`，
  用于生成 `similar_to` 边与「跨文献数值检索」。

---

## 3. 图谱功能修复方案

### 3.1 「显示为零」——已修复，附根因（保留备查）

- **根因**：`LiteratureFiguresPanel` 渲染分支 `loading ? … : papers.length === 0 ? 「文献库为空」 : <画廊>`
  未区分「固定文献模式」；而该面板**全项目只以固定模式使用**（`LiteratureKnowledgeSection` 传 `fixedPaperId`），
  固定模式的 effect 提前 return、**永不加载 `papers`** → `papers` 恒空 → 画廊永远不可达。
- **修复**（P0 已完成并实测）：分支改为 `!fixedPaperId && papers.length === 0`，固定模式按 `shown.length` 判空态；
  并在页签显示数量（`文献图片（22）`）。
- **数据侧结论**：接口与文件一直是好的（`/figures?paper_id=1529` → 22；`/figures/{id}/file` → 200 image/jpeg），
  所以**不需要重建图谱数据**，只需把它纳入新模型（`figures.item_id`）并补组织维度。

### 3.2 「文献图片」选项卡设计（已按你的决策实现，此处补完整信息架构）

```
文献卡片
├─ 头部：标题 · 期刊/年 · DOI · 来源标签 · 操作（补解析 / 上传主文SI / GNN反馈 / 编辑）
├─ 页签（顺序按你的决策）
│   ① 原文信息        —— 元数据（含摘要，已自动回填）+ 附件列表（主文/SI，可改角色）
│   ② 文献图片（N）   —— 本次新增/修复的重点
│   ③ 结构化条目（M）
│   ④ 笔记（新增，借鉴 Zotero）      ← §6 精读产出落这里
│   ⑤ 关联（新增）    —— 引用/被引/相似（借鉴 zotero-refs）
└─ 底部：所属集合 / 标签（新增，借鉴 Zotero）
```

**文献图片页签**：
- 顶栏：类型筛选（全部/结构图/光谱/机理图）+ **来源筛选（内嵌图/页渲染/上传/截图）** + 「读图提取数值」（视觉模型）+ 「上传图片」；
- 画廊：缩略图（可点开放大，**已实现 Lightbox**：大图、上/下一张、页码与来源、改标注、下载原图、←/→/Esc）；
- 标注编辑（已有，走 `PATCH /figures/{fig_id}`）：caption / tags / type，新增 `caption_source` 显示
  （`pdf`=取自 PDF 图注 / `vision`=视觉模型生成 / `user`=人工改过）。

### 3.3 图片标注功能设计

| 层级 | 机制 | 成本 |
|---|---|---|
| L1（默认，零成本） | 入库时用 **PDF 图注文本** 作 caption（已实现）；无图注则生成占位「Fig. 5（第 7 页，无图注）」 | 0 |
| L2（可选，需视觉模型） | 对无图注/用户点选的图调用 `vision.analyze_image`（**已实现**，`src/literature/vision.py`）→ 回填 caption + `caption_source=vision` + `confidence`；单次 ≤6 张 | 每图 1 次视觉调用 |
| L3（人工） | 用户在 Lightbox 或卡片上改 caption/tags（已有 PATCH） | 0 |

**视觉模型可行性（你的机器实测 + 2026 年型号对照）**

| 模型 | 量化 | 显存 | 8GB 卡结论 |
|---|---|---|---|
| **Qwen3-VL-4B**（首选） | Q4_K_M ~3.3GB | 最低 ~6GB | ✅ **舒适区**（DocVQA ~91%），Ollama tag `qwen3-vl:4b` |
| Qwen3-VL-8B | Q4_K_M ~6.1GB | 最低 8GB | ⚠️ **8GB 是下限而非舒适区**：大图/高分辨率会顶到显存墙（DocVQA 96.1%、OCRBench 89.6%，比 4B 高约 5 点，只在 OCR/表格密集时才值得） |
| Qwen3-VL-2B | ~1.9GB | 4GB 卡可用 | 轻量 OCR/描述够用，文档精度明显不如 4B |
| MiniCPM-V 4.5（3.6B） | 官方 GGUF Q4 + `mmproj` | 估算 <6GB（**未确认**） | llama.cpp 路径成熟，可作为 4B 的备选 |
| moondream2 | 1.7GB | 极低 | ❌ **上下文仅 2K**、官方自述难以理解复杂指令 → 只适合**粗粒度打标** |
| Qwen3-VL-30B-A3B | MoE Q4 ~20GB | 超 8GB | ❌ 不可行（MoE 权重需常驻） |

- **量化取舍**：视觉模型对量化比纯文本更敏感（视觉编码器也被压）。Q4_K_M ≈ OCR 精度损失 **3–5%**（可接受）；
  Q8_0 接近全精度但显存翻倍（需 16GB+）；**Q2_K 在视觉任务上不建议**（幻觉与抽错显著上升）。
  → **8GB 的现实结论：4B-Q4 做主力 + 需要高精度时按页调用云端**，比硬塞 8B 更稳。
- **接入方式（三种都暴露 OpenAI 兼容端点，我们只换 `base_url`）**：
  - **Ollama**：`http://localhost:11434/v1/`，`api_key='ollama'`（必填但被忽略）；官方确认 `/v1/chat/completions`
    **支持 Vision（`image_url` 传 base64 data URI）**。⚠️ **两个必须注意的点**：
    ① `image_url` **不支持远程 URL，只能 base64**；② **上下文长度无法通过 OpenAI 协议设置**，
    必须写 `Modelfile` 的 `PARAMETER num_ctx` 后 `ollama create`——**"整篇 PDF 转图喂进去"的场景必须调这一项**。
  - **LM Studio**（默认端口 1234）：适合人工试模型，作为产品依赖不如 Ollama 稳。
  - **vLLM**：吞吐/并发最好，但需独立环境与较大显存余量，8GB 单机不划算。
- **我们的落地封装**：统一「OpenAI 兼容客户端 + 可配 `base_url`/`model`」，云端与本地同一代码路径；
  图片统一 base64 内联；**设置页显式暴露 `num_ctx` 与「是否允许上传到云端」开关**（隐私开关必须显式）。

---

## 4. 补解析功能增强方案

### 4.1 多文件上传（主文 + 多份 SI）——已支持，增强项如下

- 现状：`POST /{paper_id}/parse`（`files[]` 多份、`role` 自动分配、最多 8 份/篇、≤20MB/份、sha1 去重、长期留存、可复用）；
- **增强**：
  1. **拖拽 + 角色自动识别**：文件名 `SI|supp|supporting|mmc|ESI` → `si`，其余 → `main`；UI 可改；
  2. **解析进度**：长文献按段解析耗时数分钟 → 前端显示「第 k/N 段」（已有秒级进度，补段级）；
  3. **附件类型扩展**：`dataset/code/other`（借鉴 Zotero 附件不限 PDF）；
  4. **失败可续**：某份 PDF 解析失败不影响其余（现已如此），失败项在附件列表标红并可单独重试。

### 4.2 摘要提取修复（已修）+ 加固

- 已修：解析成功即 `update_paper_fields(only_empty=True)` 自动回填（含 abstract），预览显示摘要与字数；
- **加固建议**：
  1. **摘要定位**：若 LLM 未返回摘要，用「Abstract/摘要」关键词附近段落二次抽取（`_META_PROMPT` 已要求 abstract，加一次定位重试）；
  2. **多来源兜底**：Crossref `abstract`（若 DOI 命中）→ LLM 抽取 → 首段启发式，三级回退；
  3. **人工兜底**：预览里提供「手动粘贴摘要」入口（写 `PATCH /papers`，`only_empty=False` 时允许覆盖）。

### 4.3 LLM 结构化提取优化（Prompt 模板见 §6.2）

- 现状：已修复推理模型预算与白名单大小写；7 种 `kind`；逐条试校验、默认只勾合法条目；
- **优化**：
  1. **分维度多次调用**（而非一次抽全）：`metadata` / `monomer_pair+film_outcome` / `conditions` /
     `characterization+metrics` / `property` / `dft` / `conclusion` 各一次，单次输出短 → 更少截断、更易校验；
  2. **JSON Schema 约束 + 修复重试**（见 §6.2）；
  3. **数值规范化**：`metrics` 抽出后统一 `name/value/unit` 并写入 `entry_metrics`（跨文献数值检索的前提）；
  4. **SMILES 纪律**：只接受正文出现的 SMILES；`monomer_pair` 缺 SMILES → 标记 invalid（已有）。

### 4.4 文献编号递增——已满足

`resolver.next_paper_id()` = `max(库内 id)+1`；内置库到 **1781** → 新文献 **1782** 起；
迁移到 SQLite 后该语义**保持不变**（`paper_id` 对外不变，`items.id` 仅内部用）。

---

## 5. 知识图谱构建方案

### 5.1 节点与关系定义（在现有基础上扩展）

**已有**（v1.9.3）：节点 `literature`（`LITP-<paper_id>`）、`reaction`、`monomer`、`solvent`、`catalyst`、`outcome`；
边 `reaction_uses_solvent` / `reaction_uses_catalyst` / `reaction_produces` / `reaction_cited_in`。

**新增（借鉴 zotero-mcp 的「文档-实体-关系」与 lit-monitor 的实体抽取）**：

| 新节点 | 说明 | 来源 |
|---|---|---|
| `technique` | 表征手段（PXRD/FTIR/BET/SEM…） | 已有 `normalize_technique` 归一化表 |
| `metric` | 数值指标实例（比表面积 2105 m²/g） | `entry_metrics` |
| `property` | 材料属性（带隙/接触角/截留率…） | `property` 条目 |
| `concept` | 主题/方法（如「溶剂热成膜」「界面聚合」） | LLM 主题抽取 + 关键词 |
| `note` | 精读笔记/结论 | 新 `notes` 表 |
| `paper`（引用层） | 与 `literature` 区分：**被引用的外部文献**（无 PDF） | Crossref/DOI |
| `author` / `journal` | 作者、期刊 | 元数据 |

**新增边**：

| 边 | 语义 | 构建方式 |
|---|---|---|
| `cites` / `cited_by` | 文献↔文献引用 | DOI 匹配 + Crossref `reference` 字段（有则用，无则留空） |
| `similar_to`（带 `score`） | 语义相似 | **向量近邻**（`embedding.py` 已有，条目级 → 聚合到文献级） |
| `reports_metric` / `reports_property` | 文献/实验组→数值/属性 | 结构化条目 → 图 |
| `uses_technique` | 文献/实验组→表征手段 | 同上 |
| `authored_by` / `published_in` | 文献→作者/期刊 | 元数据 |
| `note_of` | 笔记→文献/条目 | 新 `notes` 表 |
| `has_collection` / `has_tag` | 文献→集合/标签（图侧可见） | 新表 |

### 5.2 自动构建流程

```
① 录入（DOI/Crossref/上传 PDF/Zotero 导入）
   → items + attachments（不依赖 LLM）
② 补解析（多文件）
   → 文本层提取 → LLM 分维度结构化抽取 → entries + entry_metrics
   → 逐条试校验 annotate_entries（人工在预览里勾选/改）
③ 入图（用户确认后）
   → graph_ingest.sync_group（已有：reaction/monomer/solvent/catalyst/outcome + cited_in）
   → 新增 sync_entities：technique/metric/property/concept 节点与边
   → 新增 sync_paper_relations：cites/similar_to/authored_by/published_in
④ 精读（可选，LLM/视觉）
   → notes（人工确认后）→ note_of 边
⑤ 图查询/检索
   → query_graphrag（已有五路召回 + 图检索）
   → 新增：跨文献数值检索（entry_metrics 的 SQL）+ 相似文献（paper_vectors）
```

**关键原则**（沿用你已确认的口径）：**LLM 只提出候选，用户确认后才入图**；
图谱写入幂等（同一 `(subject, predicate, object)` 不重复）；每条边带 `meta_json.source`
（`llm_extract` / `user` / `crossref` / `embedding`）以便追溯与撤销。

### 5.3 存储方案

| 方案 | 评估 | 结论 |
|---|---|---|
| **SQLite `graph_nodes/graph_edges`（同库）+ NetworkX 侧车图** | 零安装（stdlib `sqlite3`，含 `WITH RECURSIVE`）、事务/索引/可与文献库同库联查、**FTS5** 全文、PyInstaller 友好；算法侧继续用已有 NetworkX | ✅ **推荐（本期）**。理由：我们已经有 SQLite 作为文献库主库，**再把图放同一个库**，避免引入第二个嵌入式引擎与其打包风险 |
| DuckDB + 递归 CTE | MIT、单文件、分析能力强（`USING KEY` 优化递归 CTE）；但需**额外的二进制扩展**，PyInstaller 有已知坑（`hook-duckdb.py` / "cannot find DuckDB module"），且与我们已选的 SQLite 功能重叠 | ⚠️ **仅在未来需要大规模跨文献数值分析时再评估**（届时可作为只读分析库并存） |
| **KuzuDB** | 嵌入式图库、原生 Cypher + 向量/全文 | ❌ **明确避开**：`kuzudb/kuzu` 仓库**已被 GitHub 标记 archived**（最后 push 2025-10-10，329 open issues 无人处理），社区正在迁移（The Register 报道 / FalkorDB 迁移指南）。lit-monitor 深度依赖 `kuzu>=0.6` 即前车之鉴 |
| Neo4j（社区版/嵌入式） | Cypher 与图算法生态最全 | ❌ 不推荐：**JVM + 独立进程 + 社区版 GPLv3**（Enterprise 转向 open core）三重负担，与「随桌面包一起分发」冲突 |
| FalkorDB | 若确实需要 Cypher，是 Kuzu 的替代候选 | ⚠️ 待评估（需额外服务进程） |

规模判断：内置图 5713 文献 / 6197 反应 + 用户增量（当前 33 + 22 两批）→ **1e4~1e5 节点**，
SQLite（`WITH RECURSIVE`）+ NetworkX **毫秒级一跳/二跳完全够用**；
算法层参考 zotero-mcp 的做法：**图先落库、按上限物化进内存，超限拒绝而非 OOM**。

> 说明：这一处我与调研建议**有意分歧**——调研推荐 DuckDB 存图，但我们的文献库已选 SQLite，
> 再引入 DuckDB 会多一个二进制依赖与打包面；在 1e4~1e5 节点量级上 SQLite 的递归 CTE 足够，
> 因此**单一 SQLite** 更符合"桌面单机、随包分发、可回退"的约束。若未来节点量到 1e6 或要做
> 大规模跨文献数值聚合，再把 DuckDB 作为**只读分析库**并行引入（不改主库）。

### 5.4 与成膜打分模块的联动

**已有链路**（可复用，无需重造）：
- 文献正样本 → KB 条目的「一键打分验证」（`film_outcome` 条目 → 打分页预填）；
- `src/predictor/gnn_feedback.py`：反馈队列（`data/feedback/gnn_feedback.jsonl`），
  `source ∈ {score_correction, literature_pdf, experiment_csv}`，`confirm/reject/export_csv`；
- `src/predictor/gnn_jobs.start_retrain(feedback_ids=...)`：导出 CSV → 微调 → 版本管理（激活/回退）。

**新增联动设计**：
1. **「文献 → GNN 训练集」批量入口**：在文献库支持按 `collections/tags` 筛选后，一键把
   「该集合内所有 `film_outcome=film` 条目」批量推入反馈队列（当前只能逐条点）；
2. **置信度分层**：条目带 `source`（llm_extract/user）与 `valid` → 入队时标注权重
   （用户确认过的 = 高权重，LLM 未复核 = 低权重），对应 `start_retrain` 的 `feedback_pos_w`；
3. **闭环可追溯**：每条反馈记录回指 `entry_id`/`paper_id` → 在文献卡片上显示「已用于 GNN 第 N 版」徽标；
4. **反向**：GNN 版本页面显示「本版新增来自文献的正样本 N 条」。

---

## 6. LLM 集成方案

### 6.1 架构设计

```
                    ┌──────────── 统一设置（设置页）─────────────┐
                    │ 解析 LLM（文本）   │ 视觉读图（VLM）        │
                    │ base_url/key/model │ base_url/key/model     │
                    └─────────┬──────────┴───────────┬────────────┘
                              │                      │
   ┌──────────────┐   ┌───────▼────────┐   ┌─────────▼─────────┐
   │ 云端 OpenAI  │   │ llm_extract.py │   │ vision.py          │
   │ 兼容端点     │◄──┤ (结构化抽取)    │   │ (图→描述/数值)      │
   │ 本地 Ollama/ │   │ + JSON Schema  │   │ + caption 回填      │
   │ LM Studio/vLLM│  │ + 修复重试     │   │ + 人工确认          │
   └──────────────┘   └───────┬────────┘   └─────────┬─────────┘
                              │                      │
                    ┌─────────▼──────────────────────▼─────────┐
                    │ 全部产出走「候选 → 用户确认 → 落库/入图」  │
                    │ 预览可勾选/编辑；来源与置信度随条目持久化  │
                    └──────────────────────────────────────────┘
```
- **本地 vs 云端**：同一套 OpenAI 兼容客户端（已有），只换 `base_url`；设置页两种 provider 并存
  （文本一个、视觉一个，可分别指向不同服务）；
- **缓存**：`src/llm/client.py` 已有磁盘缓存（`llm_cache/`）；结构化抽取按 `(paper_id, prompt_version, 段哈希)` 缓存，
  重解析不重复烧钱；
- **降级**：未配置 LLM → 现有 SMILES 正则降级（保留）；视觉未配置 → 只做 L1 图注（保留）。

### 6.2 Prompt 模板（分维度 + Schema 约束）

```text
[system] 你是 COF 文献信息抽取器。只输出 JSON，不得编造；原文没有的字段留空。
[user]
【任务维度】{dimension}          # metadata | monomer_pair | conditions | characterization | property | dft | conclusion
【输出 Schema】{json_schema}     # 例：characterization → {technique, sample, metrics:[{name,value,unit}], conclusion, evidence}
【证据要求】evidence 必须是原文逐字片段（≤200 字），不得改写。
【正文】
{chunk_text}

# 校验与重试
1) 先做 JSON.parse；失败 → 把错误信息回灌，重试 1 次；
2) 再做 schema 校验（必填/类型/单位枚举）；失败 → 只回灌违规字段，重试 1 次；
3) 仍失败 → 该维度标记 failed，其余维度照常入库（不因一条坏数据丢整篇）。
```
**与现有实现的衔接**：`llm_extract._PROMPT` 已含 SMILES 纪律与 technique 规范写法；
本次改造为「按维度拆调用 + Schema 校验 + 局部重试」，并把 `technique` 走 `normalize_technique` 归一化（已有）。

### 6.3 视觉模型集成

- 复用 `vision.py`（已实现：OpenAI 兼容多模态调用、预算自动扩容、严格 JSON、>5MB 拒绝、
  `confidence` 与 `notes` 回传）；新增能力：
  1. **文献图片自动标注**（§3.3 L2）→ 写 `figures.caption` + `caption_source=vision`；
  2. **科研助手图片理解**：助手上传图片时，若主模型不支持图片 → 先跑视觉模型，把
     「【图片 视觉解读】…」并入文本（纯文本模型也能"看图"）；
  3. **截图**：Electron 主进程加 `desktopCapturer` IPC（`preload.cjs` 已有 contextBridge 基础设施），
     浏览器降级 `getDisplayMedia`；
  4. 成本控制：单次 ≤6 张、图片 ≤5MB、结果缓存（同 sha1 不重复调用）。

### 6.4 用户确认机制（贯穿全部 LLM 产出）

| 产出 | 确认方式 | 现状 |
|---|---|---|
| 结构化条目 | 解析预览：逐条勾选、非法条目禁用并显示原因 | ✅ 已有 |
| 文献图片 | 预览网格勾选（内嵌图默认勾、页渲染默认不勾） | ✅ 已有 |
| 元数据回填 | 自动但**只补空字段**，预览显示 updated/skipped，可关闭 | ✅ 已有（P0） |
| 图片标注 | 卡片/ Lightbox 内可改，`caption_source` 标注来源 | ⚠️ 部分（改动入口已有，`caption_source` 待加） |
| 笔记（精读） | 新：LLM 生成 → 草稿态 → 用户确认/编辑后生效 | ❌ 需新增 |
| 图谱入图 | 新：边带 `source`，提供「撤销本次入图」入口 | ⚠️ 需新增 |

---

## 7. 实施计划

### 7.1 优先级排序（每批可独立交付、可回退）

| 批次 | 内容 | 为什么这个顺序 |
|---|---|---|
| **P0（已完成）** | 图谱渲染分支修复 + 文献图片页签（改名/移位/计数/Lightbox）+ 摘要自动回填 | 用户可感知的直接缺陷，改动小、可立即热更新 |
| **P1** | **SQLite 化 + 数据迁移**（items/attachments/figures/entries/notes/collections/tags/relations）+ 服务端检索（`q/limit/start/sort/tag/collection`）+ API 兼容层 | 一切后续能力的地基；先做「行为等价的替换」，UI 不变、风险可控 |
| **P2** | **集合/标签/笔记 + 三栏 UI**（左：集合树/标签；中：条目列表；右：详情含 5 个页签）+ 精读生成笔记 | 借鉴 Zotero 的核心体验；依赖 P1 |
| **P3** | **图谱扩展**：文献↔文献（cites/similar_to）、technique/metric/property/concept 节点、`entry_metrics` 数值检索、图查询 UI（引用/相似面板） | 依赖 P1 的 `relations/graph_*`；直接回应「文献之间缺乏关联」 |
| **P4** | **GNN 联动批量化**（集合/标签筛选 → 批量入反馈队列、权重分层、可追溯徽标） | 依赖 P3 的组织维度 |
| **P5** | **互操作**：Zotero 本地 API 导入（23119）、BibTeX/CSL-JSON 导出 | 独立、低风险、对科研工作流增益大 |
| **P6** | 视觉批（图片自动标注 L2、助手图片理解、截图） | 依赖用户选定云端/本地视觉模型 |

### 7.2 预估工时

| 批次 | 工时 | 说明 |
|---|---|---|
| P1 SQLite 化 + 迁移 + API 兼容 | **14–20 h** | 含 schema、迁移脚本（4 个文件 → DB）、双读校验、回归测试 |
| P2 集合/标签/笔记 + 三栏 UI | **16–22 h** | 前端工作量为主（含空态/拖拽/批量操作） |
| P3 图谱扩展 | **12–18 h** | 实体抽取 prompt、入图幂等、引用匹配（Crossref）、相似边（向量） |
| P4 GNN 联动批量化 | **6–8 h** | 复用现有反馈队列与重训链路 |
| P5 Zotero 互操作（导入/导出） | **8–12 h** | 本地 API 客户端 + 映射 + BibTeX/CSL-JSON 生成 |
| P6 视觉批 | **10–14 h** | 含设置页全局化、助手图片链路、截图 IPC（需重打 Electron 壳） |
| **合计** | **约 66–94 h** | 按批交付，每批都可先热更新 UI 部分给你验收 |

### 7.3 依赖关系

```
P0 ✅ ─► P1(SQLite+检索) ─┬─► P2(集合/标签/笔记 + 三栏 UI) ─► P4(GNN 批量联动)
                          ├─► P3(图谱扩展) ────────────────────┘
                          └─► P5(Zotero 导入/导出)
P6(视觉) ── 仅依赖「设置页全局视觉配置」，可与 P2/P3 并行
```

### 7.4 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| **数据迁移损坏**（11293 条目 / 1711 文献 / 55 图 / 5 附件） | 高 | 迁移前**整目录快照**（`E:\cof-build\`）；迁移脚本**只读原文件**、写新 DB；迁移后跑**双读校验**（逐字段比对计数与抽样）；保留原文件 30 天不删 |
| 「写安装目录」隐患（现在新文献直接写 `_internal/data/paper_titles.json`） | 中（升级会丢） | P1 起**一律写 `%APPDATA%` 用户目录**；启动时若发现安装目录有增量 → 自动并入用户库并提示 |
| 升级导致内置库被覆盖 | 中 | 内置库改为**只读资源**（`_internal/data/builtin_library.json`），用户增量独立存放；`items.source` 区分 |
| 引用关系数据源不稳定（Crossref 限速/缺失） | 中 | `cites` 边**尽力而为**：有则建、无则跳过并标注 `source=missing`；不阻塞主流程 |
| 视觉模型成本/隐私 | 中 | 默认关闭；单次 ≤6 张 + 缓存；本地模型可选（离线、免费） |
| SQLite 与 PyInstaller/多进程 | 中 | 单进程写（后端唯一）、WAL 模式；打包时确认 `sqlite3` 与 FTS5 可用（stdlib 自带，需在冻结自证里加一条 FTS5 检查） |
| 前端大改的体验回退 | 中 | 用现有 `scripts/rollback_ui.ps1` + 每批 UI 快照；三栏 UI 先在「我的」页内做灰度，不动其他页面 |
| 助手与图谱/文献库的耦合加深 | 低-中 | 工具层保持「只读优先、写操作二次确认」；新增工具（`search_library`/`get_item`/`list_collections`）纳入现有 `registry` 与确认门 |

---

## 8. 需要你拍板（4 件）

1. **是否接受「SQLite 单文件库」替换现有 4 个 JSON/JSONL 文件**？（我会保留原文件做只读备份 + 迁移校验；
   好处是事务/索引/检索/集合标签笔记都变成自然能力）
2. **集合体系怎么定**：完全自由建集合树（Zotero 式），还是先给一套默认集合
   （如「COF 成膜 / 表征方法 / 计算 / 综述 / 待读」）+ 自定义？（我建议后者，开箱即用）
3. **P1 是否保持 UI 不变**（先做行为等价的存储替换，让你在老界面上先验证"什么都没坏"），
   还是 P1+P2 一起上（直接给三栏新界面）？（我建议先 P1 验证，再 P2 上新 UI）
4. **文献↔文献引用关系**：允许我调用 **Crossref**（按 DOI 取 references）来建 `cites` 边吗？
   （有网络请求、免费、无 key；不调用则只能建「相似」边，无引用关系）

---

## 9. 来源清单（本报告引用的全部外部链接）

**官方文档**
- [Direct Access to the Zotero SQLite Database](https://www.zotero.org/support/dev/client_coding/direct_sqlite_database_access) —— 数据都在 `zotero.sqlite`、**外部只应只读**、表结构随版本变化、item types/fields 见 zotero-schema、附件按父 item key 分目录、每个 item 自带 notes/attachments/tags/related items
- [Zotero **Local API**（正确地址）](https://www.zotero.org/support/dev/web_api/v3/local_api) —— 开启开关与 403、23119、仅 v3、读免认证/默认不分页、写需 `POST /api/local/authorize` + `Zotero-Server-ID`、`/searches/{key}/items` 与 `/items/{key}/file` 为本地独有
- [Web API Basics](https://www.zotero.org/support/dev/web_api/v3/basics) —— 端点与查询参数（`format/include/limit/start/sort/direction/itemType/q/qmode/tag/since/includeTrashed`）、浏览器缓存
- [Full-Text Content Requests](https://www.zotero.org/support/dev/web_api/v3/fulltext_content) —— `/items/{key}/fulltext` 与 `/fulltext?since=` 的字段与状态码
- [Write Requests](https://www.zotero.org/support/dev/web_api/v3/write_requests) —— 条目 JSON 形状、relations 谓词（`owl:sameAs`/`dc:relation`/`dc:replaces`）、tags `type`、`parentItem`、key 规则
- [Item Type/Field Requests](https://www.zotero.org/support/dev/web_api/v3/types_and_fields) —— `/itemTypes`、`/itemFields`、`/itemTypeFields`、`/items/new` 与 `https://api.zotero.org/schema`
- [JSON Objects in the API](https://www.zotero.org/support/dev/web_api/json)、[Adding Files](https://www.zotero.org/support/attaching_files)、[Notes](https://www.zotero.org/support/notes) —— linkMode 早期来源、stored vs linked、子条目 vs 独立条目、child note vs standalone note
- [Zotero 数据 schema 仓库](https://github.com/zotero/zotero-schema/tree/master) —— item types 与 fields 的权威定义
- [Zotero Connector HTTP Server](https://www.zotero.org/support/dev/client_coding/connector_http_server) —— 本地 23119 端口的官方说明
- [Zotero Forums：Journal paper with supporting information](https://forums.zotero.org/discussion/comment/510471/) —— 「SI 挂同一父条目」的官方论坛依据
- [CSL/Zotero Metadata Field Index](https://aurimasv.github.io/z2csl/typeMap.xml) —— 字段名↔CSL 映射（导出 BibTeX/CSL-JSON 会用到）

**一手源码（表结构与行为的真正依据）**
- [`resource/schema/userdata.sql`](https://github.com/zotero/zotero/blob/main/resource/schema/userdata.sql) —— §1.1 全部表/字段/主外键/索引
- [`resource/schema/system.sql`](https://github.com/zotero/zotero/blob/main/resource/schema/system.sql) —— 系统字典表（`fieldFormats` 已标 unused）
- [`xpcom/attachments.js`](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/attachments.js) —— `LINK_MODE_*`（含 `EMBEDDED_IMAGE=4`）、`storage:` / `attachments:` 前缀与 `storage/<key>/` 目录
- [`xpcom/data/tags.js`](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/data/tags.js) —— `itemTags.type` 0/1、自动标签批量清除、`tagColors` 存 `SyncedSettings`、`MAX_COLORED_TAGS=9`
- [`xpcom/data/notes.js`](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/data/notes.js) —— 笔记内容为 HTML、`data-schema-version`
- [`xpcom/data/dataObjects.js`](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/data/dataObjects.js) —— relations 读取、deleted* 表、version/synced 维护

**本地 API 路由参考（社区整理，作对照实现）**
- [congcongwang0122/zotero-skill 的 local-api-routes.md](https://github.com/congcongwang0122/zotero-skill/blob/main/references/local-api-routes.md)
- [openai/plugins 的 zotero local-api-routes.md](https://github.com/openai/plugins/blob/f812c146/plugins/zotero/skills/zotero/references/local-api-routes.md)

**插件仓库（已核实，含活跃度/许可）**
- [alisoroushmd/zotero-mcp](https://github.com/alisoroushmd/zotero-mcp)（**3★**，MIT；`54yyyu/zotero-mcp` 是**同名主流项目**5090★，只做语义检索+标注、不建图）
- [amberbiology/scribl](https://github.com/amberbiology/scribl)（5★，**AGPL-3.0**；**无 LLM**，标签 DSL 策展；[JOSS doi:10.21105/joss.06645](https://doi.org/10.21105/joss.06645)）
- [yimmy23/zotero-refs](https://github.com/yimmy23/zotero-refs)（12★，**AGPL-3.0-or-later**；多源融合 + 边可信度分层，**无 LLM**）
- [steven-jianhao-li/zotero-AI-Butler](https://github.com/steven-jianhao-li/zotero-AI-Butler)（**1744★**，AGPL-3.0；队列+限速+Base64 PDF+多轮聚合）
- [congcongwang0122/zotero-skill](https://github.com/congcongwang0122/zotero-skill)（193★，**无 LICENSE**）+ [SKILL.md](https://github.com/congcongwang0122/zotero-skill/blob/main/SKILL.md)
- [Stef4678/citation-atlas](https://github.com/Stef4678/citation-atlas)（0★，**MIT**；JSON Canvas 输出）
- 本地 API 路由对照实现：[zotero-skill local-api-routes.md](https://github.com/congcongwang0122/zotero-skill/blob/main/references/local-api-routes.md)、[openai/plugins 同类整理](https://github.com/openai/plugins/blob/f812c146/plugins/zotero/skills/zotero/references/local-api-routes.md)

**本地视觉模型与推理服务**
- [Ollama OpenAI 兼容文档](https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx)（**Vision 仅 base64**；上下文需 `Modelfile` 的 `num_ctx`）、[qwen3-vl 模型页](https://ollama.com/library/qwen3-vl)、[moondream 模型页](https://ollama.com/library/moondream)
- [Qwen3-VL-4B vs 8B 显存/量化对照](https://codersera.com/blog/qwen3-vl-4b-vs-qwen3-vl-8b-benchmarks-vram-guide/)
- [MiniCPM-V 4.5 llama.cpp 部署](https://github.com/OpenSQZ/MiniCPM-V-CookBook/blob/main/deployment/llama.cpp/minicpm-v4_5_llamacpp.md)
- [LMDeploy InternVL3-8B 量化 issue](https://github.com/InternLM/lmdeploy/issues/3988)（量化风险提示）

**图存储选型依据**
- [kuzudb/kuzu（**已 archived**）](https://github.com/kuzudb/kuzu)、[The Register 报道](https://assets.theregister.com/2025/10/14/kuzudb_abandoned/)、[FalkorDB 迁移指南](https://www.falkordb.com/blog/kuzudb-to-falkordb-migration/) —— **明确避开 KuzuDB**
- [DuckDB FAQ](https://www.duckdb.org/faq)（MIT/非营利基金会）、[USING KEY 优化递归 CTE](https://www.duckdb.org/2025/05/23/using-key)、[递归 CTE 执行优化](https://www.duckdb.org/2026/08/25/how-duckdb-runs-recursive-ctes-faster)、[PyInstaller hook 实例](https://github.com/swisstopo/lg-gcover/blob/develop/hook-duckdb.py)、[DuckDB PyInstaller 已知坑](https://github.com/duckdb/duckdb/discussions/15608)
- [Neo4j open core FAQ](https://neo4j.com/open-core-and-neo4j/) / [Licensing](https://neo4j.com/licensing/) —— 社区版 GPLv3、Enterprise 转 open core

**本系统内部依据（只读实测）**
- 数据目录 `%APPDATA%\COF-Film-Recommend\data`：`literature/knowledge_entries.jsonl`（11293 条）、
  `literature/figures_index.json`（55 条）、`literature/pdfs_index.jsonl`（5 条）、`favorites/`（13 条）、
  `feedback/gnn_feedback.jsonl`；内置库 `_internal/data/paper_titles.json`（1781 篇，**新文献被追加写在这里**）
- 关键源码：`src/literature/{resolver,attachments,figures,knowledge,graph_ingest,pdf_figures,vision,embedding}.py`、
  `src/references/titles.py`、`src/predictor/{gnn_feedback,gnn_jobs}.py`、
  `api/routers/literature.py`、`webapp/src/components/mine/LiteratureFiguresPanel.tsx`
