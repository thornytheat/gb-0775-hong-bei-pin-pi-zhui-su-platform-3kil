"""签发台整克卷联试算。

在既有分数证据之上生成交班用的整克卷联：

* 包装按整克净重逐行整克化，各产地只取相邻整克（最大余数法）；
* 每卷返料独立成表：同卷各批“领走”加“留库”按产地逐列守恒，列合计
  等于原卷该产地整克；
* 无法整克闭合的工作批进入 ``blocked``，点名卡住的返料卷联；
* 已签发内容由 :mod:`app.modules.signing.ledger` 锁定，试算与旧卷
  不一致时由签发服务拒绝，保留旧版卷联。
"""
from fractions import Fraction

from .grams import adjacent_bounds, allocate_columns, allocate_rows, whole_gram

BLOCK_REASON = {
    'INPUT_DISPUTE': '投入秤单或来源有争议，先停签',
    'OUTPUT_DISPUTE': '灌装、损耗或返料秤单有争议，先停签',
    'INTAKE_MISMATCH': '投入称量与工作批总秤不符，先停签',
    'NEGATIVE_REMAINDER': '去向净重大于总秤，先停签',
    'CYCLE': '返料循环引用未拆清，先停签',
    'UPSTREAM_PENDING': '上游工作批待证，先停签',
    'FRACTIONAL_RETURN': '返料卷产地克重带小数，整克领/留无法对平原卷，先停签',
}
DRAW_ROW = 'draw'
HOLD_ROW = 'hold'


def _frac(values):
    return {key: Fraction(value) for key, value in dict(values).items()}


def _blocked_return_rolls(report, work):
    """从被隔离的投入秤单里找出该批实际卡住的返料卷联。"""
    return [{'roll': row['source'], 'ticket': row['id'], 'line': row['line'],
             'code': row['code'], 'g': row['g']}
            for row in report['supply']['rejected']
            if row.get('work') == work and row.get('source_kind') == 'return']


def _fractional_rolls(packing):
    """产地克重带小数的已核返料卷：整克拆分无法守恒，整卷停签。"""
    result = {}
    for roll, entry in packing['returns'].items():
        if entry['status'] != 'certified':
            continue
        donor = _frac(entry['origin_g'])
        fraction = {origin: str(amount) for origin, amount in donor.items()
                    if amount.denominator != 1}
        if fraction:
            result[roll] = fraction
    return result


def _rolls_drawn_by_work(trace):
    """工作批 → 它领用过的返料卷列表（按投入行顺序去重）。"""
    mapping = {}
    for work, job in trace['jobs'].items():
        rolls = []
        for feed in job['lots']:
            if feed['source_kind'] == 'return' and feed['source'] not in rolls:
                rolls.append(feed['source'])
        mapping[work] = rolls
    return mapping


def build_plan(report):
    """根据一次完整流水线报告生成整克签发试算。"""
    trace, packing = report['lineage'], report['packing']
    labels = report['supply']['origins']
    fractional_rolls = _fractional_rolls(packing)
    drawn_by_work = _rolls_drawn_by_work(trace)

    candidates, blocked = [], []
    for code, row in sorted(packing['packages'].items()):
        work = row['work']
        job = trace['jobs'][work]
        net = whole_gram(row['g'])
        stuck_rolls = _blocked_return_rolls(report, work)
        fraction_hits = [roll for roll in drawn_by_work.get(work, ())
                         if roll in fractional_rolls]
        extra_codes = []
        if fraction_hits:
            extra_codes.append('FRACTIONAL_RETURN')
        if job['codes'] or net is None or fraction_hits:
            reasons = [BLOCK_REASON.get(code, code) for code in job['codes']]
            reasons += [BLOCK_REASON['FRACTIONAL_RETURN']] * len(extra_codes)
            if not reasons:
                reasons = ['包装净重不是整克，无法只在相邻整克内取量']
            blocked.append({
                'package': code, 'work': work, 'net_g': row['g'],
                'whole_net_g': None if net is None else str(net),
                'codes': list(job['codes']) + extra_codes,
                'reasons': reasons,
                'upstream': list(job.get('upstream_pending', ())),
                'return_rolls': stuck_rolls + [
                    {'roll': roll, 'ticket': None, 'line': packing['returns'][roll]['line'],
                     'code': 'FRACTIONAL_RETURN',
                     'g': packing['returns'][roll]['g'],
                     'fraction_origin_g': fractional_rolls[roll]}
                    for roll in fraction_hits],
                'line': row['line'],
            })
            continue
        composition = _frac(row['origin_g'])
        if sum(composition.values(), Fraction()) != net:
            raise ValueError(f'{code} 产地克重合计与净重不符')
        candidates.append((code, row, net, composition))

    origins = sorted({origin for _, _, _, composition in candidates for origin in composition})

    # 包装：逐行整克化，行和等于整克净重。
    package_rows = [code for code, _, _, _ in candidates]
    package_exact = {code: {origin: composition.get(origin, Fraction()) for origin in origins}
                     for code, _, _, composition in candidates}
    package_whole = allocate_rows(package_rows, package_exact)

    plan_packages = []
    for code, row, net, composition in candidates:
        bounds = {}
        for origin in origins:
            floor, ceil = adjacent_bounds(package_exact[code][origin])
            bounds[origin] = {'floor_g': floor, 'ceil_g': ceil}
        plan_packages.append({
            'package': code, 'work': row['work'], 'net_g': str(net), 'line': row['line'],
            'exact_g': dict(row['origin_g']),
            'whole_origin_g': {origin: str(package_whole[code][origin]) for origin in origins},
            'bounds_g': bounds,
        })

    # 返料：每卷独立一张表，行 = 各批领走 + 留库，按产地逐列守恒。
    # 产地克重带小数的卷无法整克守恒，已在 blocked 点名，这里跳过。
    plan_rolls, plan_holds, plan_draws = [], [], []
    for roll, entry in sorted(packing['returns'].items()):
        if entry['status'] != 'certified' or roll in fractional_rolls:
            continue
        donor = _frac(entry['origin_g'])
        if sum(donor.values(), Fraction()) != Fraction(entry['g']):
            raise ValueError(f'{roll} 产地克重与净重不符')
        roll_origins = sorted(donor)
        draws = []
        for work, job in trace['jobs'].items():
            for feed in job['lots']:
                if feed['source_kind'] == 'return' and feed['source'] == roll:
                    draws.append((work, feed))
        draw_exact = {}
        draw_meta = {}
        for work, feed in draws:
            row_key = f'{DRAW_ROW}:{feed["ticket"]}'
            exact = _frac(feed['origin_g'])
            if set(exact) != set(roll_origins):
                raise ValueError(f'{roll} 拆投 {feed["ticket"]} 产地与原卷不一致')
            draw_exact[row_key] = exact
            draw_meta[row_key] = {'roll': roll, 'work': work, 'ticket': feed['ticket'],
                                  'line': feed['line'], 'g': feed['g'],
                                  'exact_g': dict(feed['origin_g']),
                                  'parent': feed['from_coupon'][0]['work']
                                            if feed['from_coupon'] else None}
        drawn_total = {origin: sum((exact.get(origin, Fraction())
                                    for exact in draw_exact.values()), Fraction())
                       for origin in roll_origins}
        hold_exact = {f'{HOLD_ROW}:{roll}':
                      {origin: donor[origin] - drawn_total[origin] for origin in roll_origins}}
        if any(amount < 0 for amount in hold_exact[f'{HOLD_ROW}:{roll}'].values()):
            raise ValueError(f'{roll} 留库产地克重为负')
        table = {**draw_exact, **hold_exact}
        whole = allocate_columns(roll_origins, table)

        for row_key, meta in sorted(draw_meta.items()):
            plan_draws.append({**meta,
                               'whole_origin_g': {origin: str(whole[row_key][origin])
                                                  for origin in roll_origins}})
        hold_row = f'{HOLD_ROW}:{roll}'
        plan_holds.append({'roll': roll,
                           'whole_origin_g': {origin: str(whole[hold_row][origin])
                                              for origin in roll_origins}})
        drawn_whole = sum(sum(whole[row][origin] for row in draw_exact)
                          for origin in roll_origins)
        held_whole = sum(whole[hold_row].values())
        plan_rolls.append({'roll': roll, 'work': entry['work'], 'net_g': entry['g'],
                           'drawn_g': entry['drawn_g'], 'available_g': entry['available_g'],
                           'line': entry['line'], 'origins': roll_origins,
                           'whole_drawn_g': str(drawn_whole),
                           'whole_available_g': str(held_whole),
                           'whole_origin_g': {origin: str(donor[origin])
                                              for origin in roll_origins}})

    return {'origins': origins,
            'origin_labels': {origin: labels[origin]['label'] for origin in origins},
            'packages': plan_packages, 'draws': plan_draws,
            'rolls': plan_rolls, 'holds': plan_holds, 'blocked': blocked,
            'fractional_rolls': [{'roll': roll, 'fraction_origin_g': fraction}
                                 for roll, fraction in sorted(fractional_rolls.items())]}


def verify_plan(plan):
    """断言整克卷联的全部守恒关系，供签发前复核与测试复用。"""
    for item in plan['packages']:
        grams = {k: int(v) for k, v in item['whole_origin_g'].items()}
        assert sum(grams.values()) == int(item['net_g'])
        for origin, value in grams.items():
            bound = item['bounds_g'][origin]
            assert bound['floor_g'] <= value <= bound['ceil_g']
    grouped = {}
    for item in plan['draws']:
        bucket = grouped.setdefault(item['roll'], {})
        for origin, value in item['whole_origin_g'].items():
            bucket[origin] = bucket.get(origin, 0) + int(value)
    for roll in plan['rolls']:
        donor = {k: int(v) for k, v in roll['whole_origin_g'].items()}
        held = next(item for item in plan['holds'] if item['roll'] == roll['roll'])
        for origin, total in donor.items():
            drawn = grouped.get(roll['roll'], {}).get(origin, 0)
            assert drawn + int(held['whole_origin_g'][origin]) == total
        assert int(roll['whole_drawn_g']) + int(roll['whole_available_g']) == int(roll['net_g'])
    return True
