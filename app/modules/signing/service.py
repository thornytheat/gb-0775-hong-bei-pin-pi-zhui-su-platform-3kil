"""整克卷联签发服务。

签发流程把试算结果与已签发台账逐克比对：

* 已签发包装的整克构成、已锁定返料拆投一字不能改；
* 新批要签时先重算，若重算与旧卷冲突则整单拒绝，旧版卷联保留；
* 凑不平的工作批停签，并点名卡住的返料卷联。
"""
from app.errors import RoastError

from .ledger import SigningLedger
from .plan import build_plan, verify_plan


def _plan_index(plan):
    packages = {item['package']: item for item in plan['packages']}
    draws = {(item['roll'], item['work'], item['ticket']): item for item in plan['draws']}
    rolls = {item['roll']: item for item in plan['rolls']}
    return packages, draws, rolls


def _snapshot(plan, package_codes):
    """取出本次签发要落锁的整克片段。

    只锁本次包装对应工作批的领走记录；同卷其它批未签发前不落锁，
    但其整克在重算时仍须与整张卷的守恒关系保持一致。
    """
    packages, draws, rolls = _plan_index(plan)
    sealed, target_works = [], set()
    for code in package_codes:
        item = packages[code]
        target_works.add(item['work'])
        sealed.append({'package': code, 'work': item['work'], 'net_g': item['net_g'],
                       'whole_origin_g': dict(item['whole_origin_g']),
                       'bounds_g': item['bounds_g']})
    locks = []
    touched_rolls = set()
    for (roll, work, ticket), item in sorted(draws.items()):
        if work in target_works:
            locks.append({'roll': roll, 'work': work, 'ticket': ticket,
                          'whole_origin_g': dict(item['whole_origin_g'])})
            touched_rolls.add(roll)
    return sealed, locks, sorted(touched_rolls)


def _assert_unchanged(plan, ledger):
    """重算结果与旧版卷联冲突即拒绝，不允许后签批改动已签克重。"""
    packages, draws, rolls = _plan_index(plan)
    conflicts = []
    for record in ledger['sealed']:
        current = packages.get(record['package'])
        if current is None or current['whole_origin_g'] != record['whole_origin_g'] \
                or current['net_g'] != record['net_g']:
            conflicts.append({'type': 'package', 'id': record['package']})
    for record in ledger['roll_locks']:
        current = draws.get((record['roll'], record['work'], record['ticket']))
        if current is None or current['whole_origin_g'] != record['whole_origin_g']:
            conflicts.append({'type': 'draw', 'id': f"{record['roll']}/{record['ticket']}"})
    if conflicts:
        raise RoastError('SIGNING_DRIFT',
                         '重算与已签卷联不一致，保留旧版卷联：'
                         + '、'.join(f"{item['type']}:{item['id']}" for item in conflicts), 409)


def issue(cabinet, run_id, requested):
    """签发一个或多个包装的整克卷联。"""
    document = cabinet.read(run_id)
    plan = build_plan(document['reports'])
    verify_plan(plan)
    ledger = SigningLedger(cabinet).read(run_id)
    _assert_unchanged(plan, ledger)

    packages, _, _ = _plan_index(plan)
    already = {item['package'] for item in ledger['sealed']}
    blocked = {item['package']: item for item in plan['blocked']}
    unknown, held = [], []
    for code in requested:
        if code in blocked:
            held.append(blocked[code])
        elif code not in packages:
            unknown.append(code)
    if unknown:
        raise RoastError('NO_PACKAGE', '找不到可签发包装：' + '、'.join(sorted(unknown)), 404)
    if held:
        item = held[0]
        rolls_text = '、'.join(f"{row['roll']}（{row['code']}）" for row in item['return_rolls']) \
            or '、'.join(item['codes'])
        raise RoastError('WHOLE_GRAM_BLOCKED',
                         f"{item['package']}（{item['work']}）凑不平整克，停签；卡在返料卷 {rolls_text}",
                         409, payload={'blocked': held})

    fresh = [code for code in requested if code not in already]
    if not fresh:
        return {'run': run_id, 'signed': sorted(already), 'newly_signed': [],
                'ledger': ledger, 'plan': plan, 'reused': True}
    sealed, locks, touched = _snapshot(plan, fresh)
    existing_locks = {(item['roll'], item['work'], item['ticket']): item
                      for item in ledger['roll_locks']}
    for item in locks:
        existing_locks.setdefault((item['roll'], item['work'], item['ticket']), item)
    merged = {
        'run': run_id,
        'sealed': sorted(ledger['sealed'] + sealed, key=lambda item: item['package']),
        'roll_locks': sorted(existing_locks.values(),
                             key=lambda item: (item['roll'], item['ticket'])),
        'updated_at': ledger['updated_at'],
    }
    saved = SigningLedger(cabinet).write(merged)
    return {'run': run_id, 'signed': [item['package'] for item in saved['sealed']],
            'newly_signed': fresh, 'ledger': saved, 'plan': plan,
            'touched_rolls': touched, 'reused': False}


def overview(cabinet, run_id):
    """整克签发台视图：试算 + 已签状态 + 停签清单。"""
    document = cabinet.read(run_id)
    plan = build_plan(document['reports'])
    verify_plan(plan)
    ledger = SigningLedger(cabinet).read(run_id)
    sealed = {item['package'] for item in ledger['sealed']}
    for item in plan['packages']:
        item['signed'] = item['package'] in sealed
    plan['signed_packages'] = sorted(sealed)
    plan['ledger_updated_at'] = ledger['updated_at']
    plan['run'] = run_id
    return plan
