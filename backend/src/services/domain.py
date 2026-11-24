from collections import Counter, defaultdict
from datetime import date


DEFECTS = {
    'DOT_LOW': ('凸点高度不足', '压印工位校准凸点高度，重压后用高度规逐件复测。'),
    'ARROW_REVERSED': ('触觉箭头反向', '按导览底图核对箭头方向，更换反向贴并逐件比对。'),
    'EDGE_LIFT': ('贴边翘起', '清洁粘接面后重新压贴，按作业指导书复查边缘附着。'),
    'INK_SMEAR': ('对比墨层拖污', '清洁印头并补印，按签样检查墨边和对比度。'),
}
SEVERITY_WEIGHTS = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16}
REGISTERED = '拒收登记'
DEFECT_CHECK = '缺陷复检'
REGISTER_CODE = 'NONE'
DEFAULT_COVERAGE = 80


def sample_rows() -> list[dict]:
    rows = []
    units = [(f'TG-1124-{index // 10 + 1:02d}', f'P{index % 10 + 1:03d}') for index in range(30)]

    def record(index, kind, code, severity):
        lot_id, unit_id = units[index]
        return {
            'lot_id': lot_id,
            'unit_id': unit_id,
            'inspection_date': '2026-11-24',
            'record_type': kind,
            'defect_code': code,
            'severity': severity,
        }

    for index in range(len(units)):
        rows.append(record(index, REGISTERED, REGISTER_CODE, 0))
    for start, end, code, severity in (
        (0, 12, 'DOT_LOW', 4),
        (12, 20, 'ARROW_REVERSED', 3),
        (20, 26, 'EDGE_LIFT', 2),
        (26, 30, 'INK_SMEAR', 1),
    ):
        for index in range(start, end):
            rows.append(record(index, DEFECT_CHECK, code, severity))
    for index, code, severity in (
        (0, 'EDGE_LIFT', 2), (1, 'INK_SMEAR', 1), (0, 'DOT_LOW', 3),
        (12, 'ARROW_REVERSED', 3), (20, 'EDGE_LIFT', 1),
    ):
        rows.append(record(index, DEFECT_CHECK, code, severity))
    return rows


def _read_ledger(rows):
    registered = {}
    checks = []
    for line, row in enumerate(rows, start=2):
        lot, unit_id = row.get('lot_id'), row.get('unit_id')
        if not all(isinstance(value, str) and value.strip() for value in (lot, unit_id)):
            raise ValueError(f'第 {line} 行缺少批次或单件编号，请按实物标签补齐。')
        unit = (lot.strip(), unit_id.strip())
        day_text = row.get('inspection_date')
        try:
            day = date.fromisoformat(day_text)
            if day.isoformat() != day_text:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError(f'第 {line} 行日期无效，请填写 YYYY-MM-DD 格式的实际登记或复检日期。') from None

        kind, code, severity = row.get('record_type'), row.get('defect_code'), row.get('severity')
        if kind == REGISTERED:
            if code != REGISTER_CODE or type(severity) is not int or severity != 0:
                raise ValueError(f'第 {line} 行拒收登记请填写缺陷代码 NONE、严重度 0。')
            registered[unit] = min(registered.get(unit, day), day)
        elif kind == DEFECT_CHECK:
            if not isinstance(code, str) or code not in DEFECTS:
                raise ValueError(f'第 {line} 行缺陷代码无效，请核对代码表（{"、".join(DEFECTS)}）。')
            if type(severity) is not int or severity not in SEVERITY_WEIGHTS:
                raise ValueError(f'第 {line} 行复检严重度无效，请填写 1 至 5 的整数。')
            checks.append((line, unit, day, code, severity))
        else:
            raise ValueError(f'第 {line} 行记录类型无效，请填写「拒收登记」或「缺陷复检」。')

    retained = {}
    check_counts = Counter()
    for line, unit, day, code, severity in checks:
        if unit not in registered:
            raise ValueError(f'第 {line} 行找不到 {unit[0]}/{unit[1]} 的拒收登记，请补齐登记后重跑。')
        if day < registered[unit]:
            raise ValueError(f'第 {line} 行复检日期早于拒收登记，请核对并改正两笔日期。')
        key = (unit, code)
        retained[key] = max(retained.get(key, severity), severity)
        check_counts[code] += 1
    return registered, retained, check_counts


def compute(rows: list[dict], options: dict) -> dict:
    coverage = options.get('coverage_pct', DEFAULT_COVERAGE)
    if type(coverage) is not int or not 1 <= coverage <= 100:
        raise ValueError('累计损失覆盖线请填写 1 至 100 的整数百分比。')
    if not rows:
        raise ValueError('请导入至少一条拒收登记，复检记录可随登记一并导入。')
    registered, retained, check_counts = _read_ledger(rows)

    counts = Counter()
    losses = Counter()
    defect_lots = defaultdict(set)
    classified = set()
    for (unit, code), severity in retained.items():
        counts[code] += 1
        losses[code] += SEVERITY_WEIGHTS[severity]
        classified.add(unit)
        defect_lots[code].add(unit[0])

    rejected_count = len(registered)
    pending_count = rejected_count - len(classified)
    total_loss = sum(losses.values())
    target = total_loss * coverage
    cumulative_loss = priority_loss = priority_count = 0
    details = []
    chart_points = []
    for rank, code in enumerate(sorted(losses, key=lambda code: (-losses[code], code)), start=1):
        name, action = DEFECTS[code]
        in_scope = cumulative_loss * 100 < target
        cumulative_loss += losses[code]
        status = '常规跟踪'
        if in_scope:
            priority_count += 1
            priority_loss = cumulative_loss
            status = '达到覆盖线' if cumulative_loss * 100 >= target else '本轮优先'
        details.append({
            '排名': rank,
            '缺陷代码': code,
            '缺陷': name,
            '涉及批次': '、'.join(sorted(defect_lots[code])),
            '涉及拒收件数': counts[code],
            '拒收件分母': rejected_count,
            '占拒收件（%）': round(100 * counts[code] / rejected_count, 2),
            '合并重复复检行': check_counts[code] - counts[code],
            '加权损失（点）': losses[code],
            '损失占比（%）': round(100 * losses[code] / total_loss, 2),
            '累计损失占比（%）': round(100 * cumulative_loss / total_loss, 2),
            '返修顺位': status,
            '工位动作': action if in_scope else f'纳入常规返修巡检。{action}',
        })
        chart_points.append({'label': name, 'value': losses[code]})

    priority_pct = round(100 * priority_loss / total_loss, 2) if total_loss else 0
    headline = (
        f'{rejected_count} 件拒收中有 {pending_count} 件待复检，补齐缺陷记录后可批准返修清单。'
        if pending_count else
        f'本轮优先返修 {priority_count} 类缺陷，覆盖 {priority_pct:g}% 的加权损失（门槛 {coverage}%）。'
    )
    raw_check_count = sum(check_counts.values())
    return {
        'headline': headline,
        'metrics': {
            '拒收件分母': rejected_count,
            '已有缺陷记录的拒收件': len(classified),
            '待复检拒收件': pending_count,
            '合并重复拒收登记行': len(rows) - raw_check_count - rejected_count,
            '合并重复复检行': raw_check_count - len(retained),
            '总加权损失（点）': total_loss,
            '目标覆盖（%）': coverage,
            '优先缺陷类别': priority_count,
            '实际优先覆盖（%）': priority_pct,
        },
        'rows': details,
        'notes': [
            '拒收件分母为拒收登记中不同的批次与单件编号组合，重复登记以最早日期校验复检。每类缺陷件数均除以全表拒收件分母，跨缺陷的占比可以合计超过 100%。',
            '同一批次、单件、缺陷代码的多次复检只保留最高严重度。同件不同缺陷分别贡献损失。严重度 1 至 5 对应 1、2、4、8、16 点。',
            '按加权损失降序、同分按缺陷代码升序。用未舍入的累计损失占比判断覆盖线，首次达到或超过覆盖线的类别计入本轮。',
            '待复检件保留在分母且暂未贡献损失，任何待复检件都会阻止批准。覆盖仅表示本轮清单涉及的已记录损失点数占比。',
        ],
        'chart': {'label': '缺陷加权损失 Pareto', 'unit': '点', 'points': chart_points},
        'can_publish': pending_count == 0,
    }
