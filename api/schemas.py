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


class RecordUpdate(BaseModel):
    """草稿继续编辑 / 转正式 / 正式记录更新流程与时间线（全字段可选）。"""
    status: str | None = Field(None, description="draft | final（final 走完整校验）")
    experiment_no: str | None = None
    outcome: str | None = None
    strength: str | None = None
    notes: str | None = None
    operator: str | None = None
    process_notes: str | None = None
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
