# -*- coding: utf-8 -*-
"""
adapters/test_iterate_suggest.py
================================
iterate_suggest 编排器波次 2（证据链升级）单元测试。

覆盖：
  ① failure 专家语料注入（failure_criteria 按 Class 切段 / playbook 按实验号切段）
  ② evidence_refs 白名单校验（文献模糊纠正 / 匹配不上剔除进 unverified_refs）
  ③ confidence 字段规整（0 条有效证据强制 low）
  ④ write_suggestions 落盘字段（confidence / unverified_refs）

运行:
  E:\\python3.12\\python.exe -m pytest minimax/adapters/test_iterate_suggest.py -v
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import iterate_suggest as it  # noqa: E402


# ---------------------------------------------------------------- 测试夹具

CRITERIA_MD = """# 失败判据手册 (Class A-G)

## 总体分类逻辑

递进关系说明。

## Class A — 无产物

**判据**：无可见固体。
**应对建议**：升温 +10°C 重试。

## Class C — 无定形产物

**判据**：有固体但 PXRD 无峰。
**应对建议**：换溶剂体系。

## Class G — 膜质量高

**判据**：连续光滑膜。
"""

PLAYBOOK_MD = """# 失败应对 Playbook

## 全局观察 (影响所有进行中实验的 3 个共同问题)

### 问题 1：膜反复溶解-再形成
统一应对：一次性加热不重启。

## A1 (TAPT + A6, 0.819, 自反应法)

### 预测失败模式与应对
72h 膜不连续则一次性加热 120°C × 72h。

## D7 (TFPT + TFMB, 0.596, 标准顺序)

### 预测失败模式与应对
粉红色油状物则降催化剂浓度。
"""


@pytest.fixture()
def app_root(tmp_path):
    """构造临时 App 根目录：只放 failure 语料两个文件"""
    exp = tmp_path / 'minimax' / 'experiment'
    exp.mkdir(parents=True)
    (exp / 'failure_criteria.md').write_text(CRITERIA_MD, encoding='utf-8')
    (exp / 'failure_playbook.md').write_text(PLAYBOOK_MD, encoding='utf-8')
    return tmp_path


def _rec(record_id='rec_20260722_001', failure_class=None, experiment_no=None):
    """构造一条最小实验记录"""
    return {
        'record_type': 'experiment_record',
        'record_id': record_id,
        'failure_class': failure_class,
        'experiment_no': experiment_no,
        'outcome': 'failed',
        'conditions': {},
    }


# ---------------------------------------------------------------- ① failure 语料

class TestFailureCorpus:
    def test_split_md_sections(self):
        """markdown 二级标题切块"""
        secs = it._split_md_sections(CRITERIA_MD)
        titles = [t for t, _ in secs]
        assert 'Class A — 无产物' in titles
        assert 'Class C — 无定形产物' in titles
        assert '总体分类逻辑' in titles

    def test_criteria_by_failure_class(self, app_root):
        """按 failure_class 抽对应 Class 段落"""
        records = [_rec(failure_class='A'), _rec('rec_2', failure_class='C')]
        text, n = it.retrieve_failure_corpus(records, app_root)
        assert n == 2
        assert 'Class A — 无产物' in text
        assert 'Class C — 无定形产物' in text
        assert 'Class G' not in text  # 未命中的 Class 不注入
        assert '内部失败处置经验' in text  # 标注来源

    def test_criteria_class_prefix_tolerance(self, app_root):
        """failure_class 写成 'Class A' / 'a' 也能识别"""
        records = [_rec(failure_class='Class A')]
        text, n = it.retrieve_failure_corpus(records, app_root)
        assert n == 1 and 'Class A — 无产物' in text

    def test_playbook_by_experiment_no(self, app_root):
        """按实验号抽 playbook 小节，并附带全局观察"""
        records = [_rec(failure_class='A', experiment_no='A1')]
        text, n = it.retrieve_failure_corpus(records, app_root)
        assert 'A1 (TAPT + A6' in text
        assert '全局观察' in text  # 有命中时附带共同问题小节
        assert 'D7 (TFPT' not in text
        # A1 小节 + 全局观察 + Class A 段 = 3 段
        assert n == 3

    def test_playbook_by_favorite(self, app_root):
        """favorite 带 experiment_no 时也能命中 playbook"""
        records = [_rec()]  # 记录本身无实验号
        favorite = {'experiment_no': 'D7'}
        text, n = it.retrieve_failure_corpus(records, app_root, favorite)
        assert 'D7 (TFPT + TFMB' in text

    def test_no_hit_returns_empty(self, app_root):
        """无 failure_class / 无实验号：不注入"""
        text, n = it.retrieve_failure_corpus([_rec()], app_root)
        assert text == '' and n == 0

    def test_missing_files_degrade(self, tmp_path):
        """语料文件不存在：静默降级不抛异常"""
        text, n = it.retrieve_failure_corpus(
            [_rec(failure_class='A', experiment_no='A1')], tmp_path)
        assert text == '' and n == 0


# ---------------------------------------------------------------- ② 白名单校验

class TestEvidenceWhitelist:
    def setup_method(self):
        self.records = [_rec('rec_20260722_001'), _rec('rec_20260722_002')]
        self.lit_refs = [
            {'kind': 'literature', 'ref': 'COF膜合成综述2024.pdf', 'note': '命中'},
            {'kind': 'literature', 'ref': 'lit_JACS_2023', 'note': '图节点'},
        ]
        self.graph_ids = ['rxn_0001', 'lit_JACS_2023']

    def test_exact_whitelist_refs_kept(self):
        """白名单内引用原样保留"""
        item = {'evidence_refs': [
            {'kind': 'experiment_record', 'ref': 'rec_20260722_001', 'note': ''},
            {'kind': 'literature', 'ref': 'lit_JACS_2023', 'note': ''},
        ]}
        refs, unverified, n_valid = it.normalize_evidence(
            item, self.records, self.lit_refs, self.graph_ids)
        assert len(refs) == 2 and unverified == [] and n_valid == 2

    def test_lit_fuzzy_case_space(self):
        """文献引用大小写/空白差异可模糊纠正"""
        item = {'evidence_refs': [
            {'kind': 'literature', 'ref': '  Lit_JACS_2023 ', 'note': ''},
        ]}
        refs, unverified, n_valid = it.normalize_evidence(
            item, self.records, self.lit_refs, self.graph_ids)
        assert refs[0]['ref'] == 'lit_JACS_2023'
        assert '已自动纠正' in refs[0]['note']
        assert n_valid == 1

    def test_lit_fuzzy_substring(self):
        """文献引用子串可模糊纠正"""
        item = {'evidence_refs': [
            {'kind': 'literature', 'ref': 'COF膜合成综述2024', 'note': ''},  # 缺 .pdf
        ]}
        refs, unverified, n_valid = it.normalize_evidence(
            item, self.records, self.lit_refs, self.graph_ids)
        assert refs[0]['ref'] == 'COF膜合成综述2024.pdf'
        assert n_valid == 1

    def test_fabricated_lit_ref_dropped(self):
        """编造文献引用：整条剔除并进 unverified_refs"""
        item = {'evidence_refs': [
            {'kind': 'experiment_record', 'ref': 'rec_20260722_001', 'note': ''},
            {'kind': 'literature', 'ref': 'Nature_2020_完全不存在的文献', 'note': '编'},
        ]}
        refs, unverified, n_valid = it.normalize_evidence(
            item, self.records, self.lit_refs, self.graph_ids)
        assert len(refs) == 1  # 只剩真实记录引用
        assert len(unverified) == 1
        assert unverified[0]['ref'] == 'Nature_2020_完全不存在的文献'
        assert n_valid == 1

    def test_fabricated_rec_ref_dropped(self):
        """编造实验记录引用：整条剔除并进 unverified_refs"""
        item = {'evidence_refs': [
            {'kind': 'experiment_record', 'ref': 'rec_99999999_999', 'note': ''},
        ]}
        refs, unverified, n_valid = it.normalize_evidence(
            item, self.records, self.lit_refs, self.graph_ids)
        # 全部剔除后走兜底（补白名单内真实记录/文献）
        assert all(r['ref'] != 'rec_99999999_999' for r in refs)
        assert any(u['ref'] == 'rec_99999999_999' for u in unverified)

    def test_rec_ref_fuzzy_corrected(self):
        """实验记录引用自然语言标记可模糊纠正（波次 1 行为保留）"""
        item = {'evidence_refs': [
            {'kind': 'experiment_record', 'ref': 'rec_20260722', 'note': ''},
        ]}
        refs, unverified, n_valid = it.normalize_evidence(
            item, self.records, self.lit_refs, self.graph_ids)
        assert refs[0]['ref'] in ('rec_20260722_001', 'rec_20260722_002')
        assert n_valid >= 1

    def test_empty_refs_fallback(self):
        """LLM 不给引用：兜底补白名单内真实记录 + 文献"""
        refs, unverified, n_valid = it.normalize_evidence(
            {}, self.records, self.lit_refs, self.graph_ids)
        assert refs and n_valid == len(refs)


# ---------------------------------------------------------------- ③ confidence

class TestConfidence:
    def test_dict_form_accepted(self):
        c = it.normalize_confidence(
            {'confidence': {'level': 'high', 'reason': '证据充分'}}, n_valid=2)
        assert c == {'level': 'high', 'reason': '证据充分'}

    def test_bare_string_accepted(self):
        c = it.normalize_confidence({'confidence': 'low'}, n_valid=1)
        assert c['level'] == 'low'

    def test_invalid_level_defaults_medium(self):
        c = it.normalize_confidence({'confidence': 'very-high'}, n_valid=1)
        assert c['level'] == 'medium'
        assert '默认' in c['reason']

    def test_missing_defaults_medium(self):
        c = it.normalize_confidence({}, n_valid=3)
        assert c['level'] == 'medium'

    def test_zero_evidence_forced_low(self):
        """0 条有效证据：强制 low 并标注（即使 LLM 自评 high）"""
        c = it.normalize_confidence(
            {'confidence': {'level': 'high', 'reason': '我觉得行'}}, n_valid=0)
        assert c['level'] == 'low'
        assert '强制降为 low' in c['reason']


# ---------------------------------------------------------------- ④ 落盘字段

class TestWriteSuggestions:
    def test_payload_has_confidence_and_unverified(self, tmp_path):
        """落盘建议带 confidence；编造引用剔除并记录 unverified_refs"""
        records = [_rec('rec_20260722_001')]
        lit_refs = [{'kind': 'literature', 'ref': 'real_paper.pdf', 'note': '命中'}]
        items = [{
            'type': 'literature',
            'title': '参考真实文献调整',
            'detail': '详情',
            'confidence': {'level': 'high', 'reason': '有证据'},
            'evidence_refs': [
                {'kind': 'literature', 'ref': 'real_paper.pdf', 'note': ''},
                {'kind': 'literature', 'ref': 'fake_doi_10.1234/nonexist', 'note': ''},
            ],
        }]
        written = it.write_suggestions(
            tmp_path, items, 'fav_x', records, lit_refs, [],
            batch='batch_test', graph_ref_ids=[])
        assert len(written) == 1
        doc = json.loads((tmp_path / f'{written[0]}.json').read_text(encoding='utf-8'))
        assert doc['payload']['confidence']['level'] == 'high'
        # 编造引用被剔除，原文记录在 unverified_refs
        refs = [r['ref'] for r in doc['evidence_refs']]
        assert 'fake_doi_10.1234/nonexist' not in refs
        assert doc['payload']['unverified_refs'][0]['ref'] == \
            'fake_doi_10.1234/nonexist'

    def test_no_unverified_no_field(self, tmp_path):
        """全部引用合法时不写 unverified_refs 字段"""
        records = [_rec('rec_20260722_001')]
        items = [{
            'type': 'literature', 'title': 't', 'detail': 'd',
            'confidence': 'medium',
            'evidence_refs': [
                {'kind': 'experiment_record', 'ref': 'rec_20260722_001', 'note': ''}],
        }]
        written = it.write_suggestions(
            tmp_path, items, 'fav_x', records, [], [], batch='batch_test')
        doc = json.loads((tmp_path / f'{written[0]}.json').read_text(encoding='utf-8'))
        assert 'unverified_refs' not in doc['payload']
        assert doc['payload']['confidence']['level'] == 'medium'


# ---------------------------------------------------------------- ⑤ 既有行为回归

class TestRegression:
    def test_split_sections_empty(self):
        assert it._split_md_sections('') == []

    def test_parse_llm_json(self):
        assert it.parse_llm_json('前缀 [{"type":"literature"}] 后缀') == \
            [{'type': 'literature'}]
        assert it.parse_llm_json('没有数组') is None

    def test_normalize_payload_unknown_type(self):
        t, payload = it.normalize_payload({'type': 'weird', 'title': 't',
                                           'detail': 'd'})
        assert t == 'literature'


# ---------------------------------------------------------------- ⑥ 锚定记录

class TestAnchorRecord:
    """--record-id 迭代锚定：基线突出 / favorite 推断 / 不存在报错"""

    def _anchor(self):
        """一条带完整条件的锚定记录"""
        r = _rec('rec_20260722_001', failure_class='A', experiment_no='A1')
        r['conditions'] = {'solvent_1': '甲苯', 'catalyst': '6M 乙酸',
                           'temperature_c': 120}
        r['strength'] = '无固体析出'
        r['notes'] = '72h 液面澄清'
        r['favorite_id'] = 'fav_20260722_001'
        return r

    def test_anchor_highlighted_in_prompt(self):
        """锚定记录在 prompt 中作为基线单独突出，其他记录降级为历史参考"""
        anchor = self._anchor()
        other = _rec('rec_20260722_002')
        records = [anchor, other]
        msgs = it.build_messages('这次失败了怎么调', {}, {}, records,
                                 '(证据)', [], anchor=anchor)
        user = msgs[1]['content']
        assert '基于以下这次实验迭代' in user          # 基线段标题
        assert '历史参考' in user                       # 次要段落
        # 基线段在历史参考段之前，且锚定记录先于其他记录出现
        assert user.index('基于以下这次实验迭代') < user.index('历史参考')
        assert user.index('rec_20260722_001') < user.index('rec_20260722_002')
        # 锚定记录的完整字段（条件/现象/备注/failure_class）出现在基线段
        baseline_seg = user.split('历史参考')[0]
        assert '无固体析出' in baseline_seg
        assert '72h 液面澄清' in baseline_seg
        assert 'failure_class=A' in baseline_seg
        # 白名单仍包含全部纳入记录 ID
        whitelist_seg = user.split('可引用的实验记录 ID')[1]
        assert 'rec_20260722_001' in whitelist_seg
        assert 'rec_20260722_002' in whitelist_seg

    def test_favorite_inferred_from_record(self):
        """favorite-id 缺省时从锚定记录的 favorite_id 推断"""
        anchor = self._anchor()
        a, fav = it.resolve_anchor([anchor], 'rec_20260722_001', None)
        assert a is anchor
        assert fav == 'fav_20260722_001'
        # 显式传 favorite-id 时不被覆盖
        _, fav2 = it.resolve_anchor([anchor], 'rec_20260722_001', 'fav_x')
        assert fav2 == 'fav_x'

    def test_missing_record_id_exits_nonzero(self):
        """record-id 不存在：报错并非 0 退出"""
        with pytest.raises(SystemExit) as exc:
            it.resolve_anchor([_rec('rec_20260722_001')],
                              'rec_99999999_999', None)
        assert exc.value.code != 0
