"""Independent arithmetic, cell provenance and bounded transport counterexamples."""
import datetime as dt
from decimal import Decimal
import json
from pathlib import Path
import sys

import openpyxl
import pytest

import run
from src import llm_client as L
from src.cellplan_v2.workbook import WorkbookIndex
from src.cellplan_v2.context import build_context
from src.cellplan_v2.execute import execute, expression, PlanError, parse_date
from src.cellplan_v2 import planner


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','fake-test-key')
    monkeypatch.setattr(L,'MODEL',L.APPROVED_MODEL)
    monkeypatch.setattr(L,'BILLING_HALT_REASON',None)
    monkeypatch.setattr(L,'_post',lambda *a:pytest.fail('unmocked paid request'))


@pytest.fixture
def book(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Ledger'
    ws.append(['Person','Team','Value','Birth','Event'])
    ws.append(['Same','North',Decimal('0.1'),dt.date(2000,6,15),dt.date(2020,6,14)])
    ws.append(['Same','South',Decimal('0.2'),dt.date(2000,6,15),dt.date(2020,6,15)])
    ws.append(['Total','All','=SUM(C2:C3)',None,None])
    ws['G2']='=SUM(C2,H2)'; ws['G3']='=C3+H3'; ws.merge_cells('I1:J1'); ws['I1']=10
    ws['K2']='2'; ws['K3']='10'
    other=wb.create_sheet('Lookup');other.append(['Person','Team','Factor'])
    other.append(['Same','North',2]);other.append(['Same','South',3])
    path=tmp_path/'test.xlsx';wb.save(path)
    index=WorkbookIndex(path)
    yield index
    index.close()


def plan(*steps, fmt=None):
    return dict(version=2,action='plan',steps=list(steps),result=steps[-1]['id'],format=fmt or {})


def read(region='A2:E3',**extras):
    return dict(id='r',op='read',sheet=0,range=region,columns={'person':'A','team':'B','v':'C','birth':'D','event':'E'},**extras)


def test_raw_coordinates_exact_decimal_and_uncached_formulas(book):
    assert book.value(0,'C2')==Decimal('0.1')
    assert book.value(0,'C4')==Decimal('0.3')
    assert book.value(0,'G2')==Decimal('0.1')
    assert book.value(0,'G3')==Decimal('0.2')
    p=plan(read(),dict(id='a',op='aggregate',input='r',metrics={'s':{'fn':'sum','column':'v'}}))
    assert execute(p,book)[0]=='0.3'


def test_merged_numeric_exists_once(book):
    p=plan(dict(id='r',op='read',sheet=0,range='I1:J1',columns={'a':'I','b':'J'}),
           dict(id='u',op='unpivot',input='r',columns={'a':'A','b':'B'},key='k',value='v'),
           dict(id='a',op='aggregate',input='u',metrics={'sum':{'fn':'sum','column':'v'}}))
    assert execute(p,book)[0]=='10'
    assert book.value(0,'J1',merged=True) is None


def test_age_is_at_event_and_accounts_for_birthday(book):
    p=plan(read(),dict(id='d',op='derive',input='r',columns={'age':{'op':'years','args':[{'col':'birth'},{'col':'event'}]}}),
           fmt={'columns':['age']})
    assert execute(p,book)[0]=='19; 20'


def test_composite_key_join_and_weighted_sum(book):
    p=plan(read(),dict(id='lookup',op='read',sheet='Lookup',range='A2:C3',columns={'who':'A','unit':'B','factor':'C'}),
           dict(id='j',op='join',inputs=['r','lookup'],on={'person':'who','team':'unit'}),
           dict(id='d',op='derive',input='j',columns={'weighted':{'op':'mul','args':[{'col':'v'},{'col':'r_factor'}]}}),
           dict(id='a',op='aggregate',input='d',metrics={'sum':{'fn':'sum','column':'weighted'}}))
    assert execute(p,book)[0]=='0.8'
    p['steps'][2]['on']={'person':'who'}
    with pytest.raises(PlanError,match='not unique'): execute(p,book)


def test_numeric_text_ranking_requires_declared_type(book):
    p=plan(dict(id='r',op='read',sheet=0,range='K2:K3',columns={'v':'K'}),
           dict(id='s',op='sort',input='r',by=[{'column':'v','descending':True}]),
           dict(id='t',op='take',input='s'),fmt={'columns':['v']})
    with pytest.raises(PlanError,match='explicit sort type'): execute(p,book)
    p['steps'][1]['by'][0]['type']='number'
    assert execute(p,book)[0]=='10'


def test_percent_points_and_relative_change_are_distinct():
    row={'a':Decimal('.20'),'b':Decimal('.25')}
    args=[{'col':'a'},{'col':'b'}]
    assert expression({'op':'change_ratio','args':args},row)==Decimal('.25')
    assert expression({'op':'sub','args':list(reversed(args))},row)==Decimal('.05')


def test_conditional_unit_conversion_does_not_evaluate_unused_branch():
    e={'op':'if','where':{'column':'unit','cmp':'eq','value':'mL'},
       'then':{'op':'div','args':[{'col':'v'},{'literal':1000}]},'else':{'col':'v'}}
    assert expression(e,{'unit':'mL','v':Decimal('1250')})==Decimal('1.25')
    assert expression(e,{'unit':'L','v':Decimal('1.25')})==Decimal('1.25')


def test_locale_dates_are_explicit():
    assert parse_date('03/04/2026','dmy')==dt.date(2026,4,3)
    assert parse_date('03/04/2026','mdy')==dt.date(2026,3,4)
    with pytest.raises(PlanError): parse_date('03/04/2026')


def test_complete_context_keeps_cell_and_formula_metadata(book):
    context=build_context(book,'Value?',dual=True)
    assert context['complete'] and context['omitted_rows']==0
    rows={r['r']:r for r in context['views'][0]['rows']}
    assert rows[2]['C']=={'n':'0.1'}
    assert rows[4]['C']['v']=={'n':'0.3'} and rows[4]['C']['f']=='SUM(C2:C3)'
    assert context['structure_hints_untrusted']


@pytest.fixture
def side_tables(tmp_path):
    """Receipts in A:D, a titled price catalog in K:M of the same sheet (rows do not correspond)."""
    wb=openpyxl.Workbook();ws=wb.active
    ws['A1']='receipts';ws['K2']='catalog'
    rows=[('Date','Site','Code','Qty'),('2025-01-05','North','P1',2),('2025-02-07','North','P2',3),
          ('2025-02-09','South','P1',4),('2025-03-01','North','P1',1)]
    for i,row in enumerate(rows,start=2):
        for col,value in zip('ABCD',row): ws[f'{col}{i}']=value
    for i,row in enumerate([('Kind','Price','Code'),('Wood',5,'P1'),('Stone',7,'P2')],start=3):
        for col,value in zip('KLM',row): ws[f'{col}{i}']=value
    path=tmp_path/'side.xlsx';wb.save(path)
    index=WorkbookIndex(path)
    yield index
    index.close()


def test_side_by_side_tables_are_separate_geometric_blocks(side_tables,book):
    from src.cellplan_v2.context import table_blocks
    assert [b['range'] for b in table_blocks(side_tables)]==['A1:D6','K2:M5']
    assert build_context(side_tables,'Total?')['table_blocks_untrusted'][1]=={'sheet':0,'range':'K2:M5','cells':10}
    assert 'table_blocks_untrusted' not in build_context(book,'Value?') or len(table_blocks(book))>1


def test_side_by_side_lookup_ignores_blank_title_keys(side_tables):
    p=plan(dict(id='r',op='read',sheet=0,range='A3:D6',columns={'date':'A','site':'B','code':'C','qty':'D'}),
           dict(id='c',op='read',sheet=0,range='K2:M5',columns={'kind':'K','price':'L','code':'M'}),
           dict(id='j',op='join',inputs=['r','c'],on={'code':'code'}),
           dict(id='f',op='filter',input='j',where={'column':'site','cmp':'eq','value':'North'}),
           dict(id='d',op='derive',input='f',columns={'value':{'op':'mul','args':[{'col':'qty'},{'col':'r_price'}]}}),
           dict(id='a',op='aggregate',input='d',metrics={'total':{'fn':'sum','column':'value'}}))
    assert execute(p,side_tables)[0]=='36'
    # Column letters outside the range's own columns still read the requested rows.
    p['steps'][1]['range']='K2:K5'
    assert execute(p,side_tables)[0]=='36'


def test_conditional_metrics_and_cross_join_combine_periods(side_tables):
    month=lambda m:{'column':'month','cmp':'eq','value':m}
    p=plan(dict(id='r',op='read',sheet=0,range='A3:D6',columns={'date':'A','site':'B','code':'C','qty':'D'}),
           dict(id='m',op='derive',input='r',columns={'month':{'op':'month','args':[{'col':'date'}]}}),
           dict(id='a',op='aggregate',input='m',metrics={'jan':{'fn':'sum','column':'qty','where':month('2025-01')},
                                                         'feb':{'fn':'sum','column':'qty','where':month('2025-02')}}),
           dict(id='d',op='derive',input='a',columns={'change':{'op':'sub','args':[{'col':'feb'},{'col':'jan'}]}}),
           fmt={'columns':['change']})
    assert execute(p,side_tables)[0]=='5'
    q=plan(dict(id='r',op='read',sheet=0,range='A3:D6',columns={'site':'B','qty':'D'}),
           dict(id='n',op='filter',input='r',where={'column':'site','cmp':'eq','value':'North'}),
           dict(id='s',op='filter',input='r',where={'column':'site','cmp':'eq','value':'South'}),
           dict(id='na',op='aggregate',input='n',metrics={'north':{'fn':'sum','column':'qty'}}),
           dict(id='sa',op='aggregate',input='s',metrics={'south':{'fn':'sum','column':'qty'}}),
           dict(id='x',op='join',inputs=['na','sa'],how='cross'),
           dict(id='d',op='derive',input='x',columns={'gap':{'op':'sub','args':[{'col':'north'},{'col':'r_south'}]}}),
           fmt={'columns':['gap']})
    assert execute(q,side_tables)[0]=='2'


def test_weighted_mean_uses_value_times_weight(side_tables):
    p=plan(dict(id='r',op='read',sheet=0,range='K4:M5',columns={'price':'L','code':'M'}),
           dict(id='w',op='read',sheet=0,range='C3:D6',columns={'code':'C','qty':'D'}),
           dict(id='j',op='join',inputs=['w','r'],on={'code':'code'}),
           dict(id='a',op='aggregate',input='j',metrics={'m':{'fn':'weighted_mean','column':'r_price','weight':'qty'}}),
           fmt={'decimals':2})
    assert execute(p,side_tables)[0]=='5.60'
    for metric in ({'fn':'mean','column':'r_price','weight':'qty'},{'fn':'sum','weight':'qty'},
                   {'fn':'weighted_mean','weight':'qty'},{'fn':'weighted_mean','column':'r_price'}):
        p['steps'][3]['metrics']['m']=metric
        with pytest.raises(PlanError,match='weighted_mean'): execute(p,side_tables)


def test_join_keeps_plain_right_aliases_without_collisions(side_tables):
    # L (price) is deliberately aliased as site: the left site keeps its name, the right one is r_site.
    p=plan(dict(id='r',op='read',sheet=0,range='A3:D6',columns={'site':'B','code':'C','qty':'D'}),
           dict(id='c',op='read',sheet=0,range='K2:M5',columns={'kind':'K','site':'L','code':'M'}),
           dict(id='j',op='join',inputs=['r','c'],on={'code':'code'}),
           dict(id='f',op='filter',input='j',where={'all':[{'column':'kind','cmp':'eq','value':'Wood'},
                                                           {'column':'site','cmp':'eq','value':'North'}]}),
           dict(id='d',op='derive',input='f',columns={'value':{'op':'mul','args':[{'col':'qty'},{'col':'r_site'}]}}),
           dict(id='a',op='aggregate',input='d',metrics={'total':{'fn':'sum','column':'value'}}))
    assert execute(p,side_tables)[0]=='15'


def test_malformed_plan_errors_get_repair_feedback(book,monkeypatch):
    replies=iter([plan(dict(id='c',op='cell',sheet=0,cell='C4'))]*2)
    real,calls=planner.execute,[]
    def flaky(p,b):
        calls.append(p)
        if len(calls)==1: raise AttributeError("'list' object has no attribute 'get'")
        return real(p,b)
    monkeypatch.setattr(planner,'execute',flaky)
    monkeypatch.setattr(L,'_post',lambda body,timeout:response(next(replies)))
    cache=run.TableCache();b=L.Budget()
    try: assert planner.answer('What is the total?',book.path,cache,b,'raw','q')=='0.3'
    finally: planner.close_cache(cache)
    assert b.attempts==2 and b.diagnostics['status']=='executed'


def test_unknown_columns_name_the_available_aliases(side_tables):
    p=plan(dict(id='r',op='read',sheet=0,range='A3:D6',columns={'site':'B','qty':'D'}),
           dict(id='d',op='derive',input='r',columns={'x':{'op':'mul','args':[{'col':'amount'},{'literal':2}]}}))
    with pytest.raises(PlanError,match="'amount'.*qty, site"): execute(p,side_tables)


@pytest.mark.parametrize('query,places',[
    ('Dựa trên sổ tính, tổng là bao nhiêu? Chỉ ghi số nguyên.',0),
    ('Dựa trên sổ tính, trung bình là bao nhiêu? Ghi đúng 2 chữ số thập phân.',2),
    ('Using only the workbook, what is the total? Exclude absences; return an integer.',0),
    ('What is the mean? Return two decimal places.',2),
    ('Which sites? Answer with the names separated by a comma and one space.',None),
    ('Liệt kê các số nguyên, cách nhau bởi dấu phẩy.',None),
    ('Who is youngest? Answer with only the last name.',None),
])
def test_requested_single_number_is_read_from_the_question(query,places):
    assert planner.requested_number(query)==places


def test_single_number_questions_reject_lists_and_apply_precision(side_tables,monkeypatch):
    rows=plan(dict(id='r',op='read',sheet=0,range='A3:D6',columns={'qty':'D'}),fmt={'columns':['qty']})
    total=plan(dict(id='r',op='read',sheet=0,range='A3:D6',columns={'qty':'D'}),
               dict(id='a',op='aggregate',input='r',metrics={'m':{'fn':'mean','column':'qty'}}))
    replies=iter([rows,total]);feedback=[]
    def post(body,timeout):
        sent=json.loads(body)['messages'];feedback.append(sent[-1]['content'] if len(sent)>2 else '')
        return response(next(replies))
    monkeypatch.setattr(L,'_post',post)
    cache=run.TableCache();b=L.Budget()
    try: assert planner.answer('What is the mean quantity? Return two decimal places.',side_tables.path,cache,b,'raw','q')=='2.50'
    finally: planner.close_cache(cache)
    assert b.attempts==2 and 'has 4 rows' in feedback[1]


def test_clock_time_cells_render_as_json_context(tmp_path):
    wb=openpyxl.Workbook();ws=wb.active
    ws.append(['Shift','Start']);ws.append(['Early',dt.time(8,30)])
    path=tmp_path/'times.xlsx';wb.save(path)
    index=WorkbookIndex(path)
    try:
        context=build_context(index,'When does Early start?')
        assert context['complete'] and context['views'][0]['rows'][1]['B']['v']=={'time':'08:30:00'}
    finally: index.close()


def test_internal_v2_error_is_recorded_not_raised(book,monkeypatch):
    def broken(*a,**k): raise TypeError('unexpected cell type')
    monkeypatch.setattr(planner,'build_context',broken)
    cache=run.TableCache();b=L.Budget()
    try: assert planner.answer('Total?',book.path,cache,b,'raw','q') is None
    finally: planner.close_cache(cache)
    assert b.diagnostics['status']=='index_or_context_error' and b.diagnostics['error'].startswith('TypeError')


def test_unpivot_limit_checked_before_allocating(book,monkeypatch):
    import src.cellplan_v2.execute as engine
    monkeypatch.setattr(engine,'MAX_ROWS',3)
    p=plan(dict(id='r',op='read',sheet=0,range='A2:C3',columns={'x':'B','y':'C'}),
           dict(id='u',op='unpivot',input='r',columns={'x':'X','y':'Y'}))
    with pytest.raises(PlanError,match='unpivot record limit'): execute(p,book)


def response(obj,cost=.0001):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(obj)}}],
            'usage':{'cost':cost,'prompt_tokens':100,'completion_tokens':100}}


def test_inspect_repair_and_execution_share_three_attempts(book,monkeypatch):
    good=plan(dict(id='c',op='cell',sheet=0,cell='C4'))
    replies=iter([{'version':2,'action':'inspect','regions':[{'sheet':0,'range':'A1:C4'}]},
                  plan(dict(id='c',op='cell',sheet=0,cell='ZZ999')),good])
    bodies=[]
    def post(body,timeout):
        bodies.append(json.loads(body));return response(next(replies))
    monkeypatch.setattr(L,'_post',post)
    cache=run.TableCache();budget=L.Budget()
    try:
        assert planner.answer('What is the total?',book.path,cache,budget,'dual','q')=='0.3'
    finally: planner.close_cache(cache)
    assert budget.attempts==3 and budget.actual_usd==pytest.approx(.0003)
    assert all(len(json.dumps(b,ensure_ascii=False).encode())<=L.MAX_BODY_BYTES for b in bodies)
    assert all(b['messages'][1]['content'].endswith('\n\nQUESTION:\nWhat is the total?') for b in bodies)


def test_direct_answer_is_eligible_with_valid_evidence(book,monkeypatch):
    reply={'version':2,'action':'answer','answer':'0.3','evidence':[{'sheet':0,'range':'C2:C3'}]}
    monkeypatch.setattr(L,'_post',lambda *a:response(reply))
    cache=run.TableCache();b=L.Budget()
    try: assert planner.answer('Total?',book.path,cache,b,'direct','q')=='0.3'
    finally: planner.close_cache(cache)
    assert b.diagnostics['status']=='direct_answer'


def test_model_and_base_url_are_not_silently_replaced(monkeypatch):
    saved=run._apply_openrouter_cli('fake','google/gemini-2.5-flash-lite','https://example.invalid/v1')
    seen=[]
    monkeypatch.setattr(L,'_post',lambda body,timeout:(seen.append(json.loads(body)) or response({'version':2,'action':'abstain'})))
    try:
        b=L.Budget();assert L.request_json([{'role':'user','content':'Q'}],b)[0]['action']=='abstain'
        assert seen[0]['model']=='google/gemini-2.5-flash-lite'
        assert L.URL=='https://example.invalid/v1/chat/completions'
        assert 'only' not in seen[0]['provider']
    finally: run._restore_openrouter_cli(saved)


@pytest.mark.parametrize('cap',[600,1200])
def test_configured_output_cap_reaches_the_transport(monkeypatch,cap):
    seen=[]
    monkeypatch.setattr(L,'MAX_TOKENS',cap)
    monkeypatch.setattr(L,'_post',lambda body,timeout:(seen.append(json.loads(body)) or response({'version':2,'action':'abstain'})))
    L.request_json([{'role':'user','content':'Q'}],L.Budget())
    assert seen[0]['max_tokens']==cap


def test_v2_runtime_integration_uses_existing_transport(book,monkeypatch):
    monkeypatch.setenv('DS2_SOLVER_PROFILE','raw')
    monkeypatch.setattr(L,'_post',lambda *a:response(plan(dict(id='c',op='cell',sheet=0,cell='C4'))))
    cache=run.TableCache()
    try:
        result,b=run.answer_one({'id':'q','query':'What total?','file_name':book.path.name},book.path.parent,cache,timeout=10,use_fork=False)
        assert result=='0.3' and b.attempts==1
    finally: planner.close_cache(cache)


def test_global_stable_question_cost_includes_older_source_and_resharding(tmp_path):
    sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
    import qwen_campaign as C
    ledger=C.Ledger.create(tmp_path/'ledger.json','test')
    config={'policy':'selective','output_tokens':1200,'api_enabled':True}
    # Reconstruct a genuine v1 ledger entry, before stable identities existed.
    import hashlib
    hashed=hashlib.sha256(b'x').hexdigest()
    identity={'source_sha':'a'*40,'input_sha256':'b'*64,'configuration':config,'repetition':1,
              'case_ids_sha256':hashlib.sha256(C.canonical([hashed])).hexdigest()}
    old=hashlib.sha256(C.canonical(identity)).hexdigest()
    ledger.state['invocations'][old]=dict(identity,stage='development',cases=[hashed])
    ledger.save()
    for _ in range(2):
        n=ledger.reserve('development',old,'x','c'*64);ledger.finish(n,{'usage':{'cost':'.03'}})
    # Rebinding the same input maps prior spending; changing batch and source
    # cannot obtain another fresh .10 allowance for the same semantic case.
    ledger.register('development','d'*40,'b'*64,config,['x'],case_keys={'x':'e'*64})
    new=ledger.register('development','f'*40,'1'*64,dict(config,solver_profile='dual'),['y'],case_keys={'y':'e'*64})
    n=ledger.reserve('development',new,'y','c'*64);ledger.finish(n,{'usage':{'cost':'.02'}})
    with pytest.raises(C.CampaignError,match='cumulative'): ledger.reserve('development',new,'y','c'*64)
    assert C.money(ledger.totals()['actual_usd'])==Decimal('.08')


def test_unresolved_billing_stops_other_questions(tmp_path):
    sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
    import qwen_campaign as C
    ledger=C.Ledger.create(tmp_path/'ledger.json','test')
    invocation=ledger.register('development','a'*40,'b'*64,{'policy':'selective','output_tokens':1200},['x','y'],
                               case_keys={'x':'d'*64,'y':'e'*64})
    n=ledger.reserve('development',invocation,'x','c'*64);ledger.finish(n,{})
    with pytest.raises(C.CampaignError,match='halted'): ledger.reserve('development',invocation,'y','c'*64)
    assert ledger.totals()['unresolved_usd']=='0.03'


def test_new_paid_registration_cannot_choose_legacy_identity(tmp_path):
    sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
    import qwen_campaign as C
    ledger=C.Ledger.create(tmp_path/'ledger.json','test')
    with pytest.raises(C.CampaignError,match='stable case keys'):
        ledger.register('development','a'*40,'b'*64,{'policy':'selective','output_tokens':1200},['x'])
    assert ledger.state['invocations']=={}
