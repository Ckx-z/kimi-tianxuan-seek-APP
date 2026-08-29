"""Pydantic 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PredictRequest(BaseModel):
    ald_smiles: str = Field(..., description="醛单体 SMILES")
    amine_smiles: str = Field(..., description="胺单体 SMILES")


class PairItem(BaseModel):
    ald_smiles: str
    amine_smiles: str


class BatchPredictRequest(BaseModel):
    pairs: list[PairItem]


class FavoriteCreate(BaseModel):
    aldehyde_smiles: str
    amine_smiles: str
    ald_name: str = ""
    amine_name: str = ""
    notes: str = ""
    # 归属收藏夹（缺省归兜底夹「收藏夹1」）
    folder_id: str | None = None
    # 预留 DFT 快照（本期仅透传落盘，DFT 计算后续批次接入）
    dft_snapshot: dict | None = None
    # 收藏时一并写入的当前打分快照（可选；缺省则由存储层从预测日志回填）
    score: float | None = None
    std: float | None = None
    ood: str | None = None
    score_policy: str | None = None
    tree_score: float | None = None
    gnn_score: float | None = None

class FavoriteUpdate(BaseModel):
    """收藏条目局部更新：移夹 / 改备注 / 写入 DFT 快照（全可选）。"""
    folder_id: str | None = None
    notes: str | None = None
    dft_snapshot: dict | None = None

class FavoriteCopy(BaseModel):
    """复制收藏到目标收藏夹。"""
    folder_id: str = Field(..., description="目标收藏夹 id（必须已存在）")

class RecordsBundleExport(BaseModel):
    """按收藏分组导出实验记录为一份 Word。"""
    favorite_ids: list[str] = Field(..., description="收藏 id 列表（至少 1 个）")

class FolderCreate(BaseModel):
    name: str = Field(..., description="收藏夹名称（重名拒绝）")

class FolderRename(BaseModel):
    name: str = Field(..., description="新名称（重名拒绝）")


class RecordCreate(BaseModel):
    favorite_id: str | None = None
    aldehyde_smiles: str = ""
    amine_smiles: str = ""
    conditions: dict = Field(default_factory=dict)
    outcome: str = Field("", description="film | partial | failed（draft 可留空）")
    strength: str = ""
    notes: str = ""
    operator: str = ""
    experiment_no: str = Field("", description="实验编号（final 必填，draft 可留空）")
    status: str = Field("final", description="draft 草稿暂存（宽松校验）| final 正式")
    process_notes: str = Field("", description="完整实验流程（长文本）")
    timeline: list[dict] = Field(default_factory=list, description="时间点记录条目")
    self_summary: str = Field("", description="自我总结（草稿也可填）")
    mistakes: str = Field("", description="本人认为的失误（草稿也可填）")


class RecordUpdate(BaseModel):
    """草稿继续编辑 / 转正式 / 正式记录全字段整体修改（全字段可选）。"""
    status: str | None = Field(None, description="draft | final（final 走完整校验）")
    experiment_no: str | None = None
    outcome: str | None = None
    strength: str | None = None
    notes: str | None = None
    operator: str | None = None
    process_notes: str | None = None
    self_summary: str | None = None
    mistakes: str | None = None
    conditions: dict | None = None
    timeline: list[dict] | None = None


class PlanCardRequest(BaseModel):
    aldehyde_smiles: str
    amine_smiles: str
    ald_name: str = ""
    amine_name: str = ""
    template_id: str | None = Field(
        None, description="方案卡模板 id；空则内置侯老师 v3.9")


class LLMSettings(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class SuggestRequest(BaseModel):
    question: str = Field(..., description="迭代问题原文（自然语言）")
    favorite_id: str | None = Field(
        None, description="收藏条目 id；空则基于全部实验记录")
    record_id: str | None = Field(
        None, description="锚定实验记录 id（rec_YYYYMMDD_NNN）；"
        "与 favorite_id 可同传，favorite 缺省时由编排器从记录推断")


class AdoptRequest(BaseModel):
    suggestion_id: str = Field(..., description="建议主键（sug_YYYYMMDD_NNN）")
    template_id: str | None = Field(
        None, description="方案模板 id；空则内置侯老师 v3.9")


class PropsItem(BaseModel):
    smiles: str = Field(..., description="单体 SMILES")
    name: str = ""


class PropsBatchRequest(BaseModel):
    items: list[PropsItem] = Field(
        ..., description="批量性质卡请求列表（单项非法 SMILES 不影响其他项）")


class AssistantSessionCreate(BaseModel):
    title: str | None = Field(None, description="会话标题（空则取首条消息前 20 字）")
    context: dict | None = Field(
        None, description="页⑤转入上下文：{favorite_id?, ald_smiles?, "
        "amine_smiles?, suggestion_ids?}")


class AssistantChatRequest(BaseModel):
    session_id: str | None = Field(None, description="空则新建会话")
    message: str = Field(..., description="用户消息（有附件时可为空串）")
    context: dict | None = Field(
        None, description="可选上下文（合并进会话 meta，结构同 sessions 创建）")
    attachments: list[str] | None = Field(
        None, description="附件 upload_id 列表（POST /uploads 返回，最多 3 个）")
    stream: bool = Field(True, description="固定 SSE 流式（保留字段）")


class AssistantConfirmRequest(BaseModel):
    """写操作二次确认：确认令牌一次性、绑定会话 + 参数摘要、5 分钟过期。"""
    session_id: str = Field(..., description="会话 ID（令牌绑定校验）")
    confirm_token: str = Field(..., description="tool_confirm 事件下发的令牌")
    decision: str = Field("confirm", description="confirm 执行 / cancel 取消")
    args: dict | None = Field(
        None, description="可选参数回显；与服务端存档摘要不符即拒绝（防篡改）")


class AssistantMemoryUpdate(BaseModel):
    """助手记忆更新：开关切换 / 内容整体覆写，二选一或同时。"""
    enabled: bool | None = Field(
        None, description="记忆编译与注入开关（None 表示不修改）")
    content: str | None = Field(
        None, description="memory.md 整体覆写内容（None 表示不修改）")


class AssistantNudgeDismiss(BaseModel):
    """连续失败提醒的"知道了"登记：该收藏当日不再提醒。"""
    favorite_id: str = Field(..., description="被 dismiss 的收藏条目 ID")


class DftJobCreate(BaseModel):
    """DFT 计算任务创建（2.0）：醛/胺单体 → 缩合二聚体 + 第三物质 X 类型。

    旧字段 smiles_a/smiles_b 兼容映射为 ald_smiles/amine_smiles。
    mode="pair"（任意双分子）时 ald/amine 字段位复用为分子 A/B，
    忽略 x_type 相关字段。
    """
    mode: str = Field(
        "dimer",
        description="计算模式：dimer（默认，醛胺缩合二聚体·X）"
                    "| pair（任意双分子 A···B 直接结合）")
    ald_smiles: str | None = Field(None, description="醛单体 SMILES（pair 模式为分子 A）")
    amine_smiles: str | None = Field(None, description="胺单体 SMILES（pair 模式为分子 B）")
    smiles_a: str | None = Field(None, description="旧字段：等价 ald_smiles")
    smiles_b: str | None = Field(None, description="旧字段：等价 amine_smiles")
    x_type: str = Field(
        "self_stack",
        description="第三物质类型：self_stack（默认，自身堆积）| solvent（溶剂）"
                    "| other_dimer（另一组单体的二聚体）| custom（自定义分子）")
    solvent_id: str | None = Field(None, description="x_type=solvent 时的内置溶剂 id")
    ald2_smiles: str | None = Field(None, description="x_type=other_dimer 时的醛单体 2")
    amine2_smiles: str | None = Field(None, description="x_type=other_dimer 时的胺单体 2")
    custom_smiles: str | None = Field(None, description="x_type=custom 时的自定义 SMILES")
    method: str = Field("gfn2", description="backend=xtb：gfnff（快速）| gfn2（精确，默认）；"
                                            "backend=psi4：wb97xd3bj_svp（默认，别名 precision）"
                                            "| b3lyp_631gdp（别名 literature）")
    backend: str = Field(
        "xtb",
        description="计算后端：xtb（默认，半经验快速档）| psi4（真 DFT 精度档，"
                    "需已安装 psi4-env，见 scripts/install_psi4_env.bat）")
    n_samples: int | None = Field(
        None, ge=1, le=64,
        description="复合物取向 MC 采样数（缺省=环境变量 COF_DFT_MC_SAMPLES 或 12；"
                    "1=旧单取向 UFF 初猜口径；仅 gfn2/psi4 档生效）")


class DftDraftPut(BaseModel):
    """DFT 计算页表单草稿保存（草稿结构由前端定义，后端原样存取）。"""
    draft: dict = Field(default_factory=dict, description="表单草稿 JSON（含 currentJobId）")


class LiteratureLookup(BaseModel):
    """Crossref 查询：doi 与 title 二选一。"""
    doi: str | None = Field(None, description="DOI（直接取元数据）")
    title: str | None = Field(None, description="标题（返回前 3 候选草稿）")


class LiteratureConfirm(BaseModel):
    """审核后的文献草稿（用户可改 title/authors 等）→ 入库。"""
    title: str = Field(..., description="文献标题（必填）")
    authors: list[str] = Field(default_factory=list)
    journal: str = ""
    year: int | None = None
    doi: str = ""
    url: str | None = None
    abstract: str | None = None
    source: str = Field("crossref", description="草稿来源（审计记录用）")
    reviewed_by: str = Field("", description="审核人（审计记录用）")
