"""条件推荐：混合推荐器（规则 + 案例）。

优先使用规则引擎给出基础方案，然后用案例匹配进行修正/增强。
"""

from __future__ import annotations

from .case_matcher import match_case
from .rule_engine import classify_monomer, recommend_conditions


class HybridConditionRecommender:
    """混合条件推荐器。"""

    def recommend(self, ald_smiles: str, amine_smiles: str) -> dict:
        """推荐实验条件。

        策略：
        1. 规则引擎给出基础方案
        2. 案例匹配提供历史参考
        3. 如果规则与案例冲突，以规则为准（规则是化学约束）
        """
        rule_result = recommend_conditions(ald_smiles, amine_smiles)
        case_result = match_case(ald_smiles, amine_smiles)

        # 融合：以规则为基础，用案例补充"历史案例"和"相似性说明"
        final = rule_result.copy()
        final["matched_case"] = case_result["matched_case"]
        final["case_similarity_score"] = case_result["similarity_score"]
        final["case_description"] = case_result["description"]
        final["case_notes"] = case_result["notes"]
        final["case_reference"] = case_result

        # 如果案例分数高（>0.5），用案例的溶剂比例和催化剂细节增强规则结果
        if case_result["similarity_score"] > 0.5:
            final["solvent_ratio"] = case_result["solvent_ratio"]
            final["catalyst"] = case_result["catalyst"]
            final["notes"] = f"{rule_result['notes']} 参考案例：{case_result['description']}。"

        return final


def recommend(ald_smiles: str, amine_smiles: str) -> dict:
    """便捷函数：推荐条件。"""
    recommender = HybridConditionRecommender()
    return recommender.recommend(ald_smiles, amine_smiles)


if __name__ == "__main__":
    ald = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"
    amine = "Nc1ccc(N)cc1"
    print(recommend(ald, amine))
