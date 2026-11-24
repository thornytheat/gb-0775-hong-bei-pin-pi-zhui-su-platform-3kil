"""整克取量：把分数产地克重改成相邻整克，并闭合每一组守恒关系。

交班只收整克卷联。每个产地的取量只允许落在精确份额的相邻整克内
（floor 或 ceil），通过最大余数法补齐差额，保证：

* 每个包装的各产地整克相加等于包装整克净重；
* 同一卷返料拆投多批时，各批领走的同产地整克与留库整克相加等于
  原卷该产地的整克。

两个守恒范围各自独立：包装按净重闭合，返料卷按原卷产地克重闭合，
不要求跨包装、跨卷的产地总账为整克（账上实际用量本就可能带小数）。
"""
from fractions import Fraction


def whole_gram(value):
    """整克净重：非整数净重无法在不改变净重的前提下取相邻整克。"""
    amount = Fraction(value)
    return amount if amount.denominator == 1 else None


def adjacent_bounds(amount):
    """相邻整克区间，返回 (floor, ceil)；本身为整数时上下界相同。"""
    amount = Fraction(amount)
    floor = amount.numerator // amount.denominator
    return floor, floor if amount.denominator == 1 else floor + 1


def allocate_rows(rows, exact_sets):
    """对若干行做整克化，行内各格只取相邻整克且行合计守恒。

    ``rows`` 为保持顺序的行键列表；``exact_sets`` 为
    ``{row_key: {column_key: Fraction}}``，每行精确合计必须是整克。
    返回同构的整克字典 ``{row_key: {column_key: int}}``。
    """
    result = {}
    for row in rows:
        exact = exact_sets[row]
        floors = {column: amount.numerator // amount.denominator
                  for column, amount in exact.items()}
        remainders = {column: amount - floors[column] for column, amount in exact.items()}
        total = sum(exact.values(), Fraction())
        if total.denominator != 1:
            raise ValueError(f'行合计不是整克: {row}={total}')
        need = int(total) - sum(floors.values())
        if not 0 <= need <= len(exact):
            raise ValueError(f'{row} 整克取量超出相邻整克范围')
        ranked = sorted(exact, key=lambda column: (-remainders[column],
                                                   str(exact[column]), column))
        whole = dict(floors)
        for column in ranked[:need]:
            whole[column] += 1
        result[row] = whole
    return result


def allocate_columns(columns, row_exact):
    """对每一列做整克化，列内各格只取相邻整克且列合计守恒。

    用于返料卷：``columns`` 为产地列，``row_exact`` 为
    ``{row_key: {column_key: Fraction}}``，行键通常是各批领走与留库。
    每个列的精确合计必须为整克（原卷该产地克重）。
    """
    result = {row: {} for row in row_exact}
    for column in columns:
        exact = {row: values.get(column, Fraction()) for row, values in row_exact.items()}
        floors = {row: amount.numerator // amount.denominator
                  for row, amount in exact.items()}
        remainders = {row: amount - floors[row] for row, amount in exact.items()}
        total = sum(exact.values(), Fraction())
        if total.denominator != 1:
            raise ValueError(f'列合计不是整克: {column}={total}')
        need = int(total) - sum(floors.values())
        if not 0 <= need <= len(exact):
            raise ValueError(f'{column} 整克取量超出相邻整克范围')
        ranked = sorted(exact, key=lambda row: (-remainders[row], str(exact[row]), row))
        for row in floors:
            result[row][column] = floors[row]
        for row in ranked[:need]:
            result[row][column] += 1
    return result
