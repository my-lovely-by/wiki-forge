# Wiki-Forge

一个用于构建 AI 驱动的 Markdown 知识库的 Python 工具包 —— 基于 Andrej Karpathy 提出的 LLM Wiki 模式，经过重新设计与工程化封装。本工具包提供一个通用核心引擎、一套可插拔的原语目录，以及通过配方（Recipes）组合的机制，让非技术用户只需一条命令即可创建一个 Obsidian 兼容的知识库，并接入 Claude 或其他 AI Agent 进行内容摄入、结构化管理和自动化操作。

---

## 目录

- [项目简介](#项目简介)
- [核心理念](#核心理念)
- [快速开始](#快速开始)
- [与 Claude 对话](#与-claude-对话)
- [第一周你会看到什么](#第一周你会看到什么)
- [双循环设计](#双循环设计)
- [组合模型](#组合模型)
- [CLI 命令参考](#cli-命令参考)
- [如何处理 `.proposed` 冲突文件](#如何处理-proposed-冲突文件)
- [项目架构深度解析](#项目架构深度解析)
- [文档索引](#文档索引)
- [许可证](#许可证)
- [致谢](#致谢)

---

## 项目简介

### 这是什么？

Wiki-Forge是一个 Python CLI 工具包，它的使命是：**让每个人都能拥有一个 AI 可读、可写、可操作的个人知识库**。

你是否有过这样的经历？

- 收集了大量的会议记录、笔记、食谱、行程规划，但它们散落在各个角落，永远不会再被翻阅。
- 听说了 Karpathy 的 LLM Wiki 模式（让 AI 自动整理和维护一个 Markdown 知识库），但不知道如何从零搭建。
- 尝试过用 Obsidian 管理知识，但缺乏系统化的模板和自动化流程，最终沦为"另一个空文件夹"。

Wik-iForge 就是为解决这些问题而生的。它将 LLM Wiki 模式从一个概念变成了一个**可复现、可扩展、对非技术用户友好**的工具。

### 它不是什么？

为了明确边界，Wiki-Forge **不**做以下事情：

| 它不做 | 为什么 |
|--------|--------|
| 不托管或同步知识库 | 你的知识库就是你机器上的一个文件夹，用你自己的 Git 或云同步服务管理 |
| 不内置 LLM 推理能力 | 你自带 Claude 或其他 Agent，工具包只提供工具层，不做推理层 |
| 不需要 API 密钥即可使用 | 默认知识库完全离线可用，研究集成是可选的 |
| 不自动覆盖你编辑过的文件 | 漂移检测 + 提案侧车文件是不可妥协的设计——任何对你编辑过的文件的修改都必须经过冲突审查 |
| 不锁定你到 Obsidian | 输出就是普通的 Markdown 文件和文件夹结构，任何编辑器都能用 |

### 适用人群

- **个人用户**：管理个人笔记、会议记录、食谱、旅行计划等
- **家庭用户**：共享家庭事务——餐食规划、医疗记录、待办事项、出行安排
- **职场人士**：管理利益相关者、客户反馈、决策记录、周报等专业内容

---

## 核心理念

Wiki-Forge 的设计建立在六个核心原则之上，这些原则在设计冲突时提供决策依据：

### 1. 日志即真相（Journal is the Truth）

每个知识库都有一个追加写入的 JSONL 日志文件（`.wiki.journal/journal.jsonl`），它是整个知识库的**唯一事实来源**。每一次状态变更操作（创建页面、摄入内容、运行操作）都会在修改磁盘之前先向日志追加一条事件记录。

如果日志和磁盘上的文件产生不一致，这个不一致本身就是你需要关注的信号——而不是应该被掩盖的问题。

### 2. 诚实优于能力（Honesty over Capability）

宁可发布一个精简但可靠的工具包，也不要一个需要大量免责声明的庞大系统。所有能力指标都是真实的——如果一个技能在评测中只有 60% 的准确率，我们就公开这个数字。

### 3. 依赖最小化（Dependency-minimal）

运行时依赖仅有 `pyyaml` + `pydantic>=2` + Python 标准库。新增运行时依赖需要先通过架构决策记录（ADR）审议。原因很简单：非技术用户无法排查安装故障，工具包必须在全新的 Python 3.11 环境中 `pipx install` 一次成功。

### 4. 通用核心 + 可插拔原语 + 配方组合

这不是一个单一应用——而是一个引擎加一个目录。每个面向特定受众的功能都封装在一个原语中，移除它不会破坏核心。配方组合原语，而非扩展原语。

### 5. 库而非应用（Library, not Application）

Wiki-Forge 被 Claude 作为一组可调用的原语来使用。Claude 是应用层，工具包是库层。我们不做 Agent、不做编排器、不做模型。这就是我们发布 Python 模块和 SKILL.md 文件，而不是 LLM 包装器的原因。

### 6. 吃自己的狗粮（Eat our own dogfood）

这个项目遵循它交付给用户的那套 AGENTS.md / Charter / ADR / RFC / 规范纪律。如果这套纪律在日常开发中站不住脚，那它也不适合交付给任何人。

---

## 快速开始

从零到一个可用的知识库，只需五步。

### 前置条件

- **Python 3.11 或更高版本**
- （推荐）`pipx`：用于隔离安装 CLI 工具
- （推荐）Git：用于版本管理
- （推荐）Obsidian：用于可视化浏览知识库（也可用任何 Markdown 编辑器）

### 第一步：安装

```bash
# 推荐方式：使用 pipx 隔离安装（避免依赖冲突）
pipx install wik-iforge

# 或者使用 pip 安装到当前环境
pip install wik-iforge
```

> **为什么推荐 pipx？** Wik-iForge 作为一个 CLI 工具，使用 pipx 安装可以将它和你的其他 Python 项目隔离，避免版本冲突。这是 Python 社区推荐的 CLI 工具安装方式。

### 第二步：初始化知识库

选择一个配方（Recipe），然后执行初始化命令：

```bash
# 个人知识库（最小化，推荐从这里开始）
wiki init my-vault --recipe personal

# 家庭知识库（共享家庭事务管理）
wiki init my-vault --recipe family

# 工作知识库（职场人士的项目与关系管理）
wiki init my-vault --recipe work-os

cd my-vault
```

初始化完成后，你会看到一个完整的文件夹结构。关于每个文件和目录的作用，详见下方[目录结构说明](#第一周你会看到什么)。

### 第三步：版本管理

`wiki init` 默认会在新知识库中初始化一个 Git 仓库并创建初始提交。如果你希望自行管理版本，或者还没有配置全局 Git 用户信息，可以使用 `--no-git` 参数：

```bash
wiki init my-vault --recipe personal --no-git
```

无论是否使用 Git，工具包都会自带一个合理的 `.gitignore`，它会忽略 `*.proposed` 冲突侧车文件、操作系统垃圾文件和搜索索引运行时文件。

### 第四步：打开知识库

知识库是 Obsidian 兼容的，有多种打开方式：

- **Obsidian**：打开 Obsidian → *文件 → 打开其他仓库* → 选择 `my-vault/` 文件夹
- **VS Code / Cursor / 任何编辑器**：直接打开 `my-vault/` 文件夹，它就是普通的 Markdown 文件
- **文件管理器**：直接在 Finder / 文件资源管理器中浏览

### 第五步：与 AI Agent 对话

在知识库根目录下打开 Claude Code（或任何读取 `AGENTS.md` 的 AI Agent）：

```bash
cd my-vault
claude  # 启动 Claude Code
```

Claude 会自动读取 `AGENTS.md` 和 `CORE.md`，了解这个知识库的结构和可用技能。然后你就可以开始对话了。

> 📖 **完整教程**：[`docs/guides/tutorials/tutorial-1-first-vault.md`](docs/guides/tutorials/tutorial-1-first-vault.md)（约 20 分钟），以及更深入的 [`tutorial-2-work-os-walkthrough.md`](docs/guides/tutorials/tutorial-2-work-os-walkthrough.md)。在安装之前，你也可以浏览 `examples/family-mini/` 和 `examples/work-os-mini/` 目录，预览渲染后的知识库样貌。

---

## 与 Claude 对话

以下三个示例按复杂度递增排列，你可以直接将它们粘贴到 Claude Code 中（在知识库根目录下运行）：

### 示例一：读取日志

无需任何前置设置，第一天就能用。日志是工具包的唯一事实来源——每一次状态变更操作都会在修改磁盘之前先记录一条 JSON 事件。

```text
读取 .wiki.journal/journal.jsonl，总结过去七天内这个知识库中
发生的所有事件。按事件类型分组。
```

### 示例二：摄入一份源材料

将一个文件放入 `raw/` 目录，然后通过工具包的摄入流程进行路由（这会将摄入分派记录到日志中）：

```bash
mkdir -p raw
printf '# 站会\n\n讨论了 Q3 规划。\n' > raw/standup.md
wiki ingest --as meeting raw/standup.md
```

然后在 Claude 中：

```text
读取 raw/standup.md 的日志摄入路由记录，执行 ingest-meeting 技能；
将生成的结构化页面写入 wiki/meetings/ 目录。
```

### 示例三：运行自动化操作

当你在 `wiki/meetings/` 中积累了一些本周的会议页面后：

```text
为当前 ISO 周运行 weekly-digest 技能。将生成的周报摘要写入
outputs/digests/ 目录中技能指定的路径。
```

> 默认的 `wiki run weekly-digest`（不带参数）会处理最近一个*完整的* ISO 周；上面的提示词中我们指定了当前周，这样你在第一天就能看到输出。

---

## 第一周你会看到什么

执行 `wiki init my-vault --recipe personal` 后，你将获得以下目录结构：

```
my-vault/
├── AGENTS.md                 # AI Agent 的行为合约——打开知识库时 Claude 首先读取的文件
├── CORE.md                   # 用自然语言描述"这个知识库是什么"
├── .gitignore                # 合理的默认配置——提交日志，忽略冲突侧车文件
├── .wiki.journal/            # 追加写入的事件日志，整个知识库的唯一事实来源
│   └── journal.jsonl         # 所有状态变更事件的 JSONL 追加记录
├── _templates/               # 工具包自动填充的页面模板
├── frontmatter.schema.yaml   # YAML 元数据的类型合约，定义各类页面的结构化字段
├── identity.md               # 关于你的种子页面（personal 配方特有）
├── skills/                   # 知识库侧技能——你的 AI Agent 会话运行的技能集
│   ├── ingest-meeting/       # 会议内容摄入技能
│   ├── weekly-digest/        # 周报摘要技能
│   ├── ingest-recipe/        # 食谱摄入技能
│   ├── wiki-search/          # 知识库全文搜索
│   ├── wiki-doctor/          # 知识库健康检查
│   ├── wiki-conflict/        # 冲突检测与解决
│   └── ...
└── wiki/                     # 结构化页面的存储位置
    ├── meetings/             # 会议记录页面
    ├── people/               # 人物关系页面
    ├── decisions/            # 决策记录页面
    ├── food/                 # 食谱页面
    ├── trips/                # 旅行计划页面
    └── ...
```

### 初始未创建、首次使用时出现的目录

- **`raw/`** — 非正式约定目录；你（或 AI Agent）在摄入之前将原始素材放在这里
- **`outputs/`** — 由操作合约规定（例如 `weekly-digest` 会写入 `outputs/digests/<窗口>.md`），第一次运行操作时自动创建

### 典型的第一周使用节奏

| 时间 | 动作 | 效果 |
|------|------|------|
| **第 1-2 天** | 摄入几份源材料（会议笔记、食谱、旅行计划） | `wiki/<类型>/` 目录开始填充，每个源材料生成一个结构化页面；日志中每页增加一条 `page.write` 事件 |
| **第 3-7 天** | 运行 `weekly-digest` 操作 | AI Agent 跨越你的所有会议页面，生成一份结构化周报摘要 |
| **任何时候** | 运行 `wiki doctor` | 检查磁盘上的文件是否与日志记录一致，发现并解决漂移问题 |

> 💡 **配方越深入，功能越丰富**：`personal` 配方提供基础功能；`family` 添加共享家庭原语（待办事项、跟进追踪、餐食规划）；`work-os` 添加利益相关者/客户/决策原语以及跨领域操作。

---

## 双循环设计

一个没有操作能力的 wiki 就是一个文件柜。纯捕获循环（只录入不产出）在初期有效，但很快会因为看不到可见的每周回报而被弃用。

Wiki-Forge 围绕**两个相互强化的循环**设计：

### 捕获循环（Capture Loop）

```
原始素材 → 摄入 → 结构化页面 → 存入正确的目录
```

每一次摄入都会将源材料（会议记录、食谱、文档等）转化为一个带有完整元数据（YAML frontmatter）的结构化 Markdown 页面，并存入知识库的正确位置。

### 操作循环（Operate Loop）

```
结构化页面 → 操作读取 → 派生产物（周报、餐食计划、周回顾）
```

操作（Operation）是一种合约驱动的自动化流程。它读取知识库中的结构化页面，跨页面聚合信息，产出一份新的派生文档。这个派生文档随后可以被后续操作和人类消费。

### 为什么需要两个循环？

捕获循环为操作循环提供**结构化输入**，操作循环为捕获循环提供**可见回报**。没有操作循环，你只是在收集数据；没有捕获循环，操作没有输入。两者共同形成一个正反馈飞轮。

### 人工审核不可妥协

关于 LLM 维护文档的研究表明：完全无监督的 LLM 管理会随时间导致质量退化。Wik-iForge 通过以下机制强制执行人工审核：

| 机制 | 作用 |
|------|------|
| **溯源追踪** | 每个页面的元数据中记录了创建者、来源和时间 |
| **矛盾检测** | 摄入新内容时，检测与现有页面的潜在矛盾 |
| **追加写入日志** | 所有变更不可变地记录在日志中，可追溯 |
| **漂移检测** | 任何对你编辑过的文件的修改都会生成一个 `.proposed` 侧车文件，而非静默覆盖 |

核心原则：**AI 提出建议，你来做决定。**

---

## 组合模型

三层架构组合成一个完整的知识库：

### 第一层：通用核心（Common Core）

始终安装的核心模块，提供基础能力：

- **`AGENTS.md`** — AI Agent 的行为合约，定义 Agent 在知识库中可以做什么、应该怎么做
- **追加写入日志** — 以 JSONL 格式记录所有状态变更事件，作为唯一事实来源
- **元数据模式基线** — `frontmatter.schema.yaml`，定义所有页面的元数据结构
- **通用技能**：
  - `wiki-search` — 基于 ripgrep 的全文搜索
  - `wiki-conflict` — 冲突检测与解决方案
  - `wiki-lock` — 多事件会话的锁定机制
  - `wiki-lint` — 知识库结构与规范检查
  - `wiki-doctor` — 知识库健康诊断（日志与磁盘一致性验证）
  - `ingest` — 通用摄入路由
  - `wiki-research` — 研究集成入口

### 第二层：原语（Primitives）

独立版本化的可插拔构建块，共四种类型：

| 原语类型 | 说明 | 示例 |
|----------|------|------|
| **本体（Ontology）** | 定义目录结构和文件夹形状 | `people/`、`food/`、`projects/` |
| **内容类型（Content-type）** | 一个摄入器 + 页面模板 + 元数据字段 | `meeting`（会议）、`recipe`（食谱）、`medical-record`（医疗记录） |
| **操作（Operation）** | 合约 + 技能 + 评测夹具 | `weekly-digest`（周报）、`meal-planning`（餐食规划）、`stakeholder-map-refresh`（利益相关者图谱刷新） |
| **基础设施（Infrastructure）** | 跨领域集成能力 | `research`、`research-perplexity`、`research-gemini`、`research-semantic-scholar` |

### 第三层：配方（Recipes）

配方是面向特定受众的 YAML 配置文件，负责将原语组合成一个完整的知识库。v2.0 提供三个配方：

| 配方 | 目标受众 | 包含的核心原语 |
|------|----------|----------------|
| [`personal`](recipes/personal.yaml) | 个人用户 | 会议、食谱、旅行、决策、人物 |
| [`family`](recipes/family.yaml) | 家庭用户 | 个人 + 待办事项、跟进追踪、餐食规划、医疗记录 |
| [`work-os`](recipes/work-os.yaml) | 职场人士 | 个人 + 利益相关者、客户反馈、供应商管理、决策流 |

> 📖 深入了解模块地图、日志事件格式和写入安全层，请参阅 [`docs/architecture/overview.md`](docs/architecture/overview.md)。所有基础性决策（标准库渲染、日志即真相、托管区域、漂移检测、Pydantic 模式、加法贡献、知识库根配置文件）都以架构决策记录（ADR）的形式记录在 [`docs/adr/`](docs/adr/) 中。

---

## CLI 命令参考

Wiki-Forge 提供以下命令行接口：

### 创建与管理

| 命令 | 说明 | 示例 |
|------|------|------|
| `wiki init <路径> --recipe <名称>` | 根据配方创建一个新的知识库 | `wiki init my-vault --recipe personal` |
| `wiki add <类型>:<名称>` | 向当前知识库安装一个原语 | `wiki add content-type:medical-record` |
| `wiki upgrade [--primitive <名称>]` | 将已安装的原语升级到最新版本 | `wiki upgrade --primitive ingest-meeting` |

### 内容操作

| 命令 | 说明 | 示例 |
|------|------|------|
| `wiki ingest <源文件>` | 将源材料路由到合适的摄入器 | `wiki ingest --as meeting raw/standup.md` |
| `wiki run <操作>` | 运行一个命名操作 | `wiki run weekly-digest` |
| `wiki research <查询>` | 调用配置的研究提供商 | `wiki research "最新的 AI 安全研究"` |
| `wiki search <查询>` | 在知识库中进行全文搜索 | `wiki search "Q3 规划"` |

### 诊断与维护

| 命令 | 说明 | 示例 |
|------|------|------|
| `wiki doctor` | 验证知识库状态与日志的一致性 | `wiki doctor` |
| `wiki journal {tail\|grep\|explain}` | 读取和查询知识库日志 | `wiki journal tail --lines 20` |
| `wiki resolve` | 处理 `.proposed` 冲突侧车文件的合并 | `wiki resolve` |
| `wiki lock` | 多事件会话的锁定管理 | `wiki lock` |

> 运行 `wiki <命令> --help` 查看每个命令的详细参数说明。规范性合约定义在 RFC-0001 §"CLI surface (target)"中。

---

## 如何处理 `.proposed` 冲突文件

Wiki-Forge **永远不会**静默覆盖你编辑过的文件。

当你的编辑与工具包最后已知的状态产生漂移时，下一次写入会以 `<路径>.proposed` 的形式保存在原始文件旁边，`wiki doctor` 会标记这个冲突。你需要决定：

1. **保留你的版本**：删除 `.proposed` 文件
2. **使用工具包的版本**：将 `.proposed` 的内容复制到原文件
3. **手动合并**：打开两个文件，手动选择保留哪些修改，然后删除 `.proposed`

详细的处理流程（包括一个基于 `examples/conflict-pending/` 知识库的完整实操案例）请参阅 [`docs/guides/how-to/resolve-a-conflict.md`](docs/guides/how-to/resolve-a-conflict.md)。

---

## 项目架构深度解析

如果你是开发者或对内部实现感兴趣，以下是关键架构决策的索引：

### 模块地图

```
wiki-forge/
├── core/                      # 通用核心模块
│   └── files/
│       ├── AGENTS.md          # AI Agent 行为合约模板
│       ├── CORE.md            # 知识库描述模板
│       ├── skills/            # 跨配方通用技能
│       └── ...
├── templates/                 # 原语目录
│   ├── ontology/              # 本体原语（目录结构定义）
│   ├── content-type/          # 内容类型原语（摄入器 + 模板）
│   ├── operation/             # 操作原语（合约 + 技能）
│   └── infrastructure/        # 基础设施原语（研究集成等）
├── recipes/                   # 配方目录
│   ├── personal.yaml          # 个人配方
│   ├── family.yaml            # 家庭配方
│   └── work-os.yaml           # 工作配方
├── llm_wiki_kit/              # Python 包源码
│   ├── cli.py                 # CLI 入口
│   ├── journal.py             # 日志引擎
│   ├── write_helper.py        # 安全写入与漂移检测
│   ├── render.py              # 模板渲染引擎
│   ├── primitives.py          # 原语加载与管理
│   └── recipes.py             # 配方解析与组合
├── tests/                     # 测试套件
│   ├── unit/                  # 单元测试
│   ├── integration/           # 集成测试
│   └── evals/                 # AI 评测（驱动 Claude Code 子进程）
├── docs/                      # 项目文档
│   ├── CHARTER.md             # 项目章程（使命、范围、原则）
│   ├── ROADMAP.md             # 路线图
│   ├── architecture/          # 架构文档
│   ├── adr/                   # 架构决策记录
│   ├── rfc/                   # 请求征求意见稿
│   ├── specs/                 # 规范文档
│   └── guides/                # 用户指南（教程、操作指南、参考、解释）
└── examples/                  # 示例知识库
    ├── family-mini/           # 家庭知识库示例
    └── work-os-mini/          # 工作知识库示例
```

### 关键架构决策

| 决策 | 说明 | 文档位置 |
|------|------|----------|
| 日志即真相 | 所有状态变更先写日志，再改磁盘 | [`docs/adr/`](docs/adr/) |
| 安全写入 | 通过 `write_helper.safe_write()` 路由所有写入，实现漂移检测 | [`docs/specs/safe-write-ordering/spec.md`](docs/specs/safe-write-ordering/spec.md) |
| 托管区域 | 文件中由工具包管理的区域与用户编辑区域严格分离 | [`docs/architecture/overview.md`](docs/architecture/overview.md) |
| 加法贡献 | 新配方只能添加原语，不能修改已有原语的行为 | [`docs/adr/`](docs/adr/) |

---

## 文档索引

| 文档类别 | 位置 | 说明 |
|----------|------|------|
| 项目章程 | [`docs/CHARTER.md`](docs/CHARTER.md) | 使命、范围和核心原则 |
| 教程 | [`docs/guides/tutorials/`](docs/guides/tutorials/) | 从零开始的完整教程 |
| 操作指南 | [`docs/guides/how-to/`](docs/guides/how-to/) | 具体操作的分步指南 |
| 架构文档 | [`docs/architecture/overview.md`](docs/architecture/overview.md) | 项目架构全景图 |
| 决策记录 | [`docs/adr/`](docs/adr/) | 架构决策记录（ADR） |
| 请求征求意见 | [`docs/rfc/`](docs/rfc/) | RFC 文档 |
| 规范文档 | [`docs/specs/`](docs/specs/) | 功能规范 |
| 路线图 | [`docs/ROADMAP.md`](docs/ROADMAP.md) | 未来规划 |
| 变更日志 | [`CHANGELOG.md`](CHANGELOG.md) | 已发布的工作记录 |

