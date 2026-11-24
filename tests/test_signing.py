from fractions import Fraction

import pytest

from app.errors import RoastError
from app.modules.signing.grams import (
    adjacent_bounds, allocate_columns, allocate_rows, whole_gram,
)
from app.modules.signing.ledger import SigningLedger
from app.modules.signing.plan import build_plan, verify_plan
from app.modules.signing.service import issue, overview
from app.modules.runs.service import seal_run


@pytest.fixture
def archived(tmp_path, files):
    from app.modules.runs.cabinet import Cabinet
    cabinet = Cabinet(tmp_path / 'signing-cabinet')
    key = seal_run(cabinet, files)['id']
    return cabinet, key


# ---------- 整克取量算法 ----------

def test_allocate_rows_sums_to_whole_net():
    exact = {'PK': {'ET-01': Fraction('3597/50'), 'CO-04': Fraction('88/5'),
                    'BR-08': Fraction('9273/50')}}
    whole = allocate_rows(['PK'], exact)['PK']
    assert sum(whole.values()) == 275
    for origin, amount in exact['PK'].items():
        floor, ceil = adjacent_bounds(amount)
        assert floor <= whole[origin] <= ceil


def test_every_cell_stays_inside_adjacent_grams():
    exact = {'r1': {'a': Fraction('1.6'), 'b': Fraction('2.4')},
             'r2': {'a': Fraction('3.2'), 'b': Fraction('0.8')}}
    result = allocate_rows(['r1', 'r2'], exact)
    for row, columns in exact.items():
        assert sum(result[row].values()) == 4
        for column, amount in columns.items():
            floor, ceil = adjacent_bounds(amount)
            assert floor <= result[row][column] <= ceil


def test_exact_integer_share_never_moves():
    exact = {'r': {'a': Fraction(2), 'b': Fraction('1.5'), 'c': Fraction('0.5')}}
    result = allocate_rows(['r'], exact)['r']
    # 整数份额 a 不参与余数分配；b、c 余数相同，按列键确定性打破并列
    assert result['a'] == 2 and result == {'a': 2, 'b': 1, 'c': 1}
    assert sum(result.values()) == 4


def test_allocate_columns_conserves_each_origin_total():
    rows = {'draw:1': {'a': Fraction('1.6'), 'b': Fraction('2.4')},
            'draw:2': {'a': Fraction('1.4'), 'b': Fraction('1.6')},
            'hold': {'a': Fraction(3), 'b': Fraction(4)}}
    result = allocate_columns(['a', 'b'], rows)
    assert result['draw:1']['a'] + result['draw:2']['a'] + result['hold']['a'] == 6
    assert result['draw:1']['b'] + result['draw:2']['b'] + result['hold']['b'] == 8
    for row, columns in rows.items():
        for column, amount in columns.items():
            floor, ceil = adjacent_bounds(amount)
            assert floor <= result[row][column] <= ceil


def test_fractional_row_total_rejected():
    with pytest.raises(ValueError):
        allocate_rows(['r'], {'r': {'a': Fraction('1.4')}})


def test_whole_gram_gate():
    assert whole_gram('275') == Fraction(275)
    assert whole_gram('3597/50') is None
    assert adjacent_bounds(Fraction('2.3')) == (2, 3)
    assert adjacent_bounds(Fraction(2)) == (2, 2)


# ---------- 整克卷联试算 ----------

def test_plan_package_origin_grams_are_whole_and_balanced(dirty):
    plan = build_plan(dirty)
    verify_plan(plan)
    for item in plan['packages']:
        grams = {k: int(v) for k, v in item['whole_origin_g'].items()}
        assert sum(grams.values()) == int(item['net_g'])
        for origin, value in grams.items():
            bound = item['bounds_g'][origin]
            assert bound['floor_g'] <= value <= bound['ceil_g']


def test_plan_keeps_fractional_share_for_audit(dirty):
    plan = build_plan(dirty)
    pk_b = next(item for item in plan['packages'] if item['package'] == 'PK-B')
    assert pk_b['exact_g']['BR-08'] == '324/5'
    assert pk_b['whole_origin_g']['BR-08'] == '65'
    assert pk_b['bounds_g']['BR-08'] == {'floor_g': 64, 'ceil_g': 65}


def test_return_roll_split_conserves_origin_grams(dirty):
    plan = build_plan(dirty)
    rt_a = next(item for item in plan['rolls'] if item['roll'] == 'RT-A')
    draws = [item for item in plan['draws'] if item['roll'] == 'RT-A']
    hold = next(item for item in plan['holds'] if item['roll'] == 'RT-A')
    donor = {k: int(v) for k, v in rt_a['whole_origin_g'].items()}
    for origin, total in donor.items():
        drawn = sum(int(item['whole_origin_g'][origin]) for item in draws)
        assert drawn + int(hold['whole_origin_g'][origin]) == total
    assert int(rt_a['whole_drawn_g']) + int(rt_a['whole_available_g']) == int(rt_a['net_g']) == 300


def test_multi_batch_draws_of_same_roll_each_adjacent(dirty):
    plan = build_plan(dirty)
    draws = {(item['work'], item['ticket']): item
             for item in plan['draws'] if item['roll'] == 'RT-A'}
    exact = {('BA-02', 'IN-03'): {'BR-08': 72, 'ET-01': 108},
             ('BA-03', 'IN-05'): {'BR-08': 48, 'ET-01': 72}}
    for key, expected in exact.items():
        assert {k: int(v) for k, v in draws[key]['whole_origin_g'].items()} == expected


def test_undrawn_roll_keeps_full_origin_stock(dirty):
    plan = build_plan(dirty)
    rt_d = next(item for item in plan['rolls'] if item['roll'] == 'RT-D')
    hold = next(item for item in plan['holds'] if item['roll'] == 'RT-D')
    assert rt_d['whole_drawn_g'] == '0' and rt_d['whole_available_g'] == '100'
    assert hold['whole_origin_g'] == {'ET-01': '100'}


def test_fractional_return_roll_blocks_its_packages(dirty):
    plan = build_plan(dirty)
    assert [item['roll'] for item in plan['fractional_rolls']] == ['RT-B']
    blocked = next(item for item in plan['blocked'] if item['package'] == 'PK-C')
    assert 'FRACTIONAL_RETURN' in blocked['codes']
    assert blocked['return_rolls'][0]['roll'] == 'RT-B'


def test_pending_job_packages_are_blocked_with_rolls(dirty):
    plan = build_plan(dirty)
    # BA-05/BA-06 争抢 RT-D 被整组隔离，其包装不进入可签清单
    signable = {item['package'] for item in plan['packages']}
    assert {'PK-E', 'PK-F', 'PK-G'}.isdisjoint(signable)


# ---------- 签发台账与锁定 ----------

def test_issue_signs_and_locks_whole_grams(archived):
    cabinet, key = archived
    result = issue(cabinet, key, ['PK-A'])
    assert result['newly_signed'] == ['PK-A'] and result['signed'] == ['PK-A']
    ledger = SigningLedger(cabinet).read(key)
    assert ledger['sealed'][0]['package'] == 'PK-A'
    assert ledger['sealed'][0]['whole_origin_g'] == {'BR-08': '240', 'CO-04': '0', 'ET-01': '360'}


def test_later_batch_cannot_change_signed_grams(archived):
    cabinet, key = archived
    issue(cabinet, key, ['PK-B'])
    ledger_before = SigningLedger(cabinet).read(key)
    # 再签其它包装：重算结果与已签内容一致时放行，且已签克重原样保留
    issue(cabinet, key, ['PK-D'])
    ledger_after = SigningLedger(cabinet).read(key)
    sealed_b = next(item for item in ledger_after['sealed'] if item['package'] == 'PK-B')
    sealed_b_before = next(item for item in ledger_before['sealed'] if item['package'] == 'PK-B')
    assert sealed_b == sealed_b_before
    assert [item['package'] for item in ledger_after['sealed']] == ['PK-B', 'PK-D']


def test_recompute_drift_rejects_and_keeps_old_ledger(archived):
    cabinet, key = archived
    issue(cabinet, key, ['PK-B'])
    path = cabinet.path / 'signing' / f'{key}.json'
    import json
    from app.modules.runs.fingerprint import checksum, encode
    document = json.loads(path.read_text(encoding='utf-8'))
    document['sealed'][0]['whole_origin_g']['ET-01'] = '999'
    document['checksum'] = checksum(encode({k: v for k, v in document.items() if k != 'checksum'}))
    path.write_text(encode(document), encoding='utf-8')
    with pytest.raises(RoastError) as error:
        issue(cabinet, key, ['PK-D'])
    assert error.value.code == 'SIGNING_DRIFT'
    preserved = json.loads(path.read_text(encoding='utf-8'))
    assert preserved['sealed'][0]['whole_origin_g']['ET-01'] == '999'
    assert [item['package'] for item in preserved['sealed']] == ['PK-B']


def test_blocked_package_refused_with_roll_named(archived):
    cabinet, key = archived
    with pytest.raises(RoastError) as error:
        issue(cabinet, key, ['PK-C'])
    assert error.value.code == 'WHOLE_GRAM_BLOCKED'
    assert 'RT-B' in str(error.value)
    assert error.value.payload['blocked'][0]['package'] == 'PK-C'


def test_issue_is_idempotent(archived):
    cabinet, key = archived
    first = issue(cabinet, key, ['PK-A'])
    second = issue(cabinet, key, ['PK-A'])
    assert not first['reused'] and second['reused']
    assert second['newly_signed'] == []


def test_unknown_package_404(archived):
    cabinet, key = archived
    with pytest.raises(RoastError) as error:
        issue(cabinet, key, ['PK-Z'])
    assert error.value.code == 'NO_PACKAGE' and error.value.status == 404


def test_overview_marks_signed_packages(archived):
    cabinet, key = archived
    issue(cabinet, key, ['PK-A', 'PK-D'])
    plan = overview(cabinet, key)
    signed = {item['package']: item['signed'] for item in plan['packages']}
    assert signed == {'PK-A': True, 'PK-B': False, 'PK-D': True}
    assert plan['signed_packages'] == ['PK-A', 'PK-D']


def test_ledger_checksum_detects_corruption(archived):
    cabinet, key = archived
    issue(cabinet, key, ['PK-A'])
    path = cabinet.path / 'signing' / f'{key}.json'
    import json
    document = json.loads(path.read_text(encoding='utf-8'))
    document['sealed'][0]['net_g'] = '1'
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(RoastError) as error:
        SigningLedger(cabinet).read(key)
    assert error.value.code == 'DAMAGED_LEDGER'


# ---------- HTTP 接口 ----------

def test_signing_api_happy_path(client):
    key = client.post('/api/runs', json={'sample': 'dirty'}).json['id']
    overview_response = client.get(f'/api/signing/runs/{key}')
    assert overview_response.status_code == 200
    response = client.post(f'/api/signing/runs/{key}/issue', json={'packages': ['PK-A']})
    assert response.status_code == 200 and response.json['signed'] == ['PK-A']
    page = client.get(f'/ledger/proof?run={key}').get_data(as_text=True)
    assert '交班整克卷联' in page and '已签发' in page


def test_signing_api_validates_body(client):
    key = client.post('/api/runs', json={'sample': 'dirty'}).json['id']
    response = client.post(f'/api/signing/runs/{key}/issue', json={'packages': []})
    assert response.status_code == 400 and response.json['code'] == 'BAD_REQUEST'


def test_proof_page_lists_blocking_roll(client):
    key = client.post('/api/runs', json={'sample': 'dirty'}).json['id']
    page = client.get(f'/ledger/proof?run={key}').get_data(as_text=True)
    assert '停签批与卡点' in page and 'RT-B' in page and 'PK-C' in page


# ---------- 合成数据：同卷三批拆投，分数份额、整克守恒 ----------

def _three_way_split_files():
    return {'files': {
        'jobs': 'work,label,measured,unit\nBA-01,制卷批,100,g\nBA-02,拆投甲,33,g\n'
                'BA-03,拆投乙,33,g\nBA-04,拆投丙,34,g',
        'origins': 'origin,label,available,unit\nA,甲产地,1000,g\nB,乙产地,1000,g',
        'inputs': 'ticket,work,source_kind,source,quantity,unit\n'
                  'IN-01,BA-01,origin,A,60,g\nIN-02,BA-01,origin,B,40,g\n'
                  'IN-03,BA-02,return,RT-X,33,g\nIN-04,BA-03,return,RT-X,33,g\n'
                  'IN-05,BA-04,return,RT-X,34,g\n',
        'outputs': 'ticket,work,kind,gross,tare,unit\nRT-X,BA-01,return,100,0,g\n'
                   'PK-2,BA-02,pack,33,0,g\nPK-3,BA-03,pack,33,0,g\n'
                   'PK-4,BA-04,pack,34,0,g\n',
    }}


def test_three_way_split_whole_gram_conservation():
    from app.pipeline import compute
    from app.source import receive
    report = compute(receive(_three_way_split_files()))
    plan = build_plan(report)
    verify_plan(plan)
    assert not plan['blocked'] and not plan['fractional_rolls']
    roll = plan['rolls'][0]
    hold = plan['holds'][0]
    for origin, total in (('A', 60), ('B', 40)):
        drawn = sum(int(item['whole_origin_g'][origin]) for item in plan['draws'])
        assert drawn + int(hold['whole_origin_g'][origin]) == total
        assert int(roll['whole_origin_g'][origin]) == total
    # 19.8 与 20.4 都只能取 20；13.2 取 13、13.6 取 14
    by_work = {item['work']: item['whole_origin_g'] for item in plan['draws']}
    assert by_work['BA-02'] == {'A': '20', 'B': '13'}
    assert by_work['BA-04'] == {'A': '20', 'B': '14'}


def test_three_way_split_signing_locks_each_batch(tmp_path):
    from app.modules.runs.cabinet import Cabinet
    from app.source import receive
    cabinet = Cabinet(tmp_path / 'split')
    key = seal_run(cabinet, receive(_three_way_split_files()))['id']
    first = issue(cabinet, key, ['PK-2'])
    assert [lock['work'] for lock in first['ledger']['roll_locks']] == ['BA-02']
    issue(cabinet, key, ['PK-3', 'PK-4'])
    ledger = SigningLedger(cabinet).read(key)
    assert [item['package'] for item in ledger['sealed']] == ['PK-2', 'PK-3', 'PK-4']
    # 三批领走的同产地整克相加等于原卷（留库为零）
    for origin, total in (('A', 60), ('B', 40)):
        assert sum(int(lock['whole_origin_g'][origin]) for lock in ledger['roll_locks']) == total
