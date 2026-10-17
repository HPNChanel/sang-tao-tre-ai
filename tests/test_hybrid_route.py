"""Routing of the hybrid profile between the baseline legacy sequence and CellPlanV2."""
import json

import openpyxl
import pytest

import run
from src import llm_client as L
from src import plan_review
from src.cellplan_v2 import planner


TABLES = []


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','fake-test-key')
    monkeypatch.setenv('DS2_SOLVER_PROFILE','hybrid')
    monkeypatch.setattr(L,'MODEL',L.APPROVED_MODEL)
    monkeypatch.setattr(L,'BILLING_HALT_REASON',None)
    monkeypatch.setattr(L,'_post',lambda *a:pytest.fail('unmocked paid request'))
    monkeypatch.setattr(run.seams,'have_executor',lambda:True)


@pytest.fixture
def workbook(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Data'
    ws.append(['Item','Qty']); ws.append(['a',1]); ws.append(['b',2])
    path = tmp_path/'route.xlsx'; wb.save(path)
    return path


def ask(path, query='How many?'):
    cache = run.TableCache()
    try:
        return run.answer_one({'id':'q','query':query,'file_name':path.name},path.parent,cache,timeout=30,use_fork=False)
    finally:
        planner.close_cache(cache)


def legacy(monkeypatch, rule=None, review=True, resolved=False, reviewed='reviewed', checked=None):
    calls = []
    monkeypatch.setattr(run,'_load_into_parent',lambda *a:TABLES)
    monkeypatch.setattr(run,'_answer_rule',lambda *a:(calls.append('rule'),rule)[1])
    monkeypatch.setattr(plan_review,'needs_review',lambda *a:(calls.append('needs_review'),review)[1])
    monkeypatch.setattr(run,'_legacy_resolved',lambda *a:(calls.append('resolved'),resolved)[1])
    monkeypatch.setattr(run,'_checked_rule',lambda *a:(calls.append('checked'),checked)[1])
    monkeypatch.setattr(run,'_answer_llm',lambda q,tables,*a:(calls.append(('review',tables)),reviewed)[1])
    return calls


def v2(monkeypatch, result):
    calls = []
    def answer(query, path, cache, budget, profile, qid):
        calls.append(profile)
        budget.diagnostics = {'profile':profile,'status':'executed' if result is not None else 'abstained'}
        return result
    monkeypatch.setattr(planner,'answer',answer)
    return calls


def test_rule_answer_without_review_is_returned_unchanged(workbook,monkeypatch):
    calls = legacy(monkeypatch,rule='42',review=False)
    v2_calls = v2(monkeypatch,'never')
    result,budget = ask(workbook)
    assert result=='42' and calls==['rule','needs_review'] and v2_calls==[]
    assert budget.diagnostics=={'route':'legacy_rule'} and budget.attempts==0


def test_natively_resolved_question_keeps_the_checked_rule_without_review(workbook,monkeypatch):
    calls = legacy(monkeypatch,rule='42',review=True,resolved=True,checked='41')
    v2_calls = v2(monkeypatch,'never')
    result,budget = ask(workbook)
    assert result=='41' and calls==['rule','needs_review','resolved','checked'] and v2_calls==[]
    assert budget.diagnostics=={'route':'legacy_checked_rule'} and budget.attempts==0


@pytest.mark.parametrize('checked',[None,''])
def test_resolved_plan_that_fails_the_checks_goes_to_cellplan_v2(workbook,monkeypatch,checked):
    calls = legacy(monkeypatch,rule='42',review=True,resolved=True,checked=checked)
    v2_calls = v2(monkeypatch,'7')
    result,budget = ask(workbook)
    assert result=='7' and v2_calls==[run.HYBRID_V2_PROFILE]
    assert calls==['rule','needs_review','resolved','checked']
    assert budget.diagnostics['route']=='cellplan_v2' and budget.diagnostics['legacy_checked_rule']=='rejected'
    assert 'fallback' not in budget.diagnostics


def test_rejected_checked_rule_and_cellplan_v2_abstention_reach_the_review(workbook,monkeypatch):
    calls = legacy(monkeypatch,rule='42',review=True,resolved=True,checked=None,reviewed='legacy')
    v2(monkeypatch,None)
    result,budget = ask(workbook)
    assert result=='legacy' and calls[-1]==('review',TABLES)
    assert budget.diagnostics['fallback']=='legacy_review' and budget.diagnostics['legacy_checked_rule']=='rejected'


def test_checked_rule_error_counts_as_a_rejection(monkeypatch):
    def broken(*a): raise KeyError('missing column')
    monkeypatch.setattr(plan_review,'safe_rule',broken)
    assert run._checked_rule('q',TABLES,'f.xlsx') is None


def test_resolved_question_end_to_end_makes_no_paid_call(workbook,monkeypatch):
    """Real parse, rule plan, checks and execution; only the review gate is forced open."""
    monkeypatch.setattr(plan_review,'needs_review',lambda *a:True)
    monkeypatch.setattr(planner,'answer',lambda *a:pytest.fail('CellPlanV2 must not run'))
    result,budget = ask(workbook,'What is the Qty of b?')
    assert result=='2' and budget.attempts==0 and budget.diagnostics=={'route':'legacy_checked_rule'}


@pytest.mark.parametrize('rule,review',[(None,True),('42',True)])
def test_unresolved_question_goes_to_cellplan_v2(workbook,monkeypatch,rule,review):
    calls = legacy(monkeypatch,rule=rule,review=review,resolved=False)
    v2_calls = v2(monkeypatch,'7')
    result,budget = ask(workbook)
    assert result=='7' and v2_calls==[run.HYBRID_V2_PROFILE]
    assert not any(isinstance(c,tuple) for c in calls)
    assert budget.diagnostics['route']=='cellplan_v2' and 'fallback' not in budget.diagnostics


def test_cellplan_v2_abstention_falls_back_to_the_baseline_review(workbook,monkeypatch):
    calls = legacy(monkeypatch,rule=None,resolved=False,reviewed='legacy')
    v2(monkeypatch,None)
    result,budget = ask(workbook)
    assert result=='legacy' and calls[-1]==('review',TABLES)
    assert budget.diagnostics['route']=='cellplan_v2' and budget.diagnostics['fallback']=='legacy_review'


def test_legacy_stage_error_still_reaches_cellplan_v2(workbook,monkeypatch):
    calls = legacy(monkeypatch)
    def broken(*a): raise ValueError('unreadable sheet')
    monkeypatch.setattr(run,'_load_into_parent',broken)
    v2(monkeypatch,'5')
    result,budget = ask(workbook)
    assert result=='5' and calls==[]
    assert budget.diagnostics['route']=='cellplan_v2' and budget.diagnostics['legacy_error']=='ValueError'


def test_legacy_error_and_cellplan_v2_abstention_leave_the_answer_blank(workbook,monkeypatch):
    calls = legacy(monkeypatch)
    def broken(*a): raise ValueError('unreadable sheet')
    monkeypatch.setattr(run,'_load_into_parent',broken)
    v2(monkeypatch,None)
    result,_ = ask(workbook)
    assert result=='' and calls==[]


def test_timeout_in_the_legacy_stage_ends_the_question(workbook,monkeypatch):
    legacy(monkeypatch)
    def slow(*a): raise run._QueryTimedOut()
    monkeypatch.setattr(run,'_load_into_parent',slow)
    v2_calls = v2(monkeypatch,'5')
    result,_ = ask(workbook)
    assert result=='' and v2_calls==[]


def test_without_a_key_the_hybrid_profile_is_the_rule_baseline(workbook,monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY')
    calls = legacy(monkeypatch,rule='42',review=True,resolved=True)
    v2_calls = v2(monkeypatch,'never')
    result,_ = ask(workbook)
    assert result=='42' and calls==['rule'] and v2_calls==[]


def test_hybrid_end_to_end_uses_the_existing_transport(workbook,monkeypatch):
    """Real legacy parse and routing; the paid call is the CellPlanV2 plan request."""
    monkeypatch.setattr(run,'_legacy_resolved',lambda *a:False)
    monkeypatch.setattr(run,'_answer_rule',lambda *a:None)
    plan = dict(version=2,action='plan',steps=[dict(id='c',op='cell',sheet=0,cell='B3')],result='c',format={})
    bodies = []
    def post(body,timeout):
        bodies.append(json.loads(body))
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(plan)}}],
                'usage':{'cost':.0001,'prompt_tokens':100,'completion_tokens':100}}
    monkeypatch.setattr(L,'_post',post)
    result,budget = ask(workbook,'What is the quantity of b?')
    assert result=='2' and budget.attempts==1 and budget.diagnostics['route']=='cellplan_v2'
    assert bodies[0]['messages'][1]['content'].endswith('\n\nQUESTION:\nWhat is the quantity of b?')


def test_ledger_and_transport_accept_the_hybrid_profile_only(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
    import campaign_wire as W
    import qwen_campaign as C
    ledger = C.Ledger.create(tmp_path/'ledger.json','test')
    config = {'policy':'selective','output_tokens':1200}
    ledger.register('development','a'*40,'b'*64,dict(config,solver_profile='hybrid'),['x'],case_keys={'x':'d'*64})
    with pytest.raises(C.CampaignError,match='solver profile'):
        ledger.register('development','a'*40,'c'*64,dict(config,solver_profile='mixed'),['y'],case_keys={'y':'e'*64})
    files = {'queries.json':'f'*64}
    manifest = {'schema_version':1,'harness_sha':'a'*40,'sources':{'B':{'sha':'b'*40}},
                'batches':[{'files':files,'input_sha256':W.input_digest(files),'ids':['x'],'stable_case_keys':{'x':'d'*64}}],
                'runs':[{'number':0,'label':'B','batch_index':0,
                         'configuration':dict(config,api_enabled=True,solver_profile='hybrid')}]}
    W.validate_manifest(manifest)
    manifest['runs'][0]['configuration']['solver_profile'] = 'mixed'
    with pytest.raises(ValueError,match='solver profile'):
        W.validate_manifest(manifest)
