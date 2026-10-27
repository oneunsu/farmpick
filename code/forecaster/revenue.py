"""사용자 입력 가정에 따른 수취액 계산. 시세·세금·수익 예측 모델이 아니다."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CALCULATION_VERSION = 'receipts-krw-half-up-v1'


def decimal_value(value, label, *, maximum=Decimal('1e14')):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > maximum:
            raise ValueError
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'{label} 값이 올바르지 않습니다.') from None


def won(value):
    return value.quantize(Decimal('1'), rounding=ROUND_HALF_UP)


def calculate_receipts(plan, channels):
    harvest = decimal_value(plan.harvest_kg, '수확량')
    percent = decimal_value(plan.saleable_percent, '판매가능 비율', maximum=Decimal(100))
    if harvest <= 0:
        raise ValueError('수확량은 0보다 커야 합니다.')
    saleable = (harvest * percent / 100).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
    production = None if plan.production_cost is None else won(decimal_value(plan.production_cost, '생산비'))
    rows = []
    for channel in channels:
        price = decimal_value(channel.price_per_kg, '판매단가')
        commission = decimal_value(channel.commission_percent, '수수료율', maximum=Decimal(100))
        gross = won(saleable * price)
        fee = won(gross * commission / 100)
        packing = won(saleable * decimal_value(channel.packing_per_kg, '포장비'))
        other = won(decimal_value(channel.other_cost, '기타 출하비용'))
        fuel_cost = Decimal(0)
        toll = Decimal(0)
        if channel.transport_mode == 'hired':
            transport = won(decimal_value(channel.freight_cost, '위탁 운송비'))
        elif channel.transport_mode == 'self':
            distance = decimal_value(channel.round_trip_km, '왕복 거리')
            efficiency = decimal_value(channel.fuel_efficiency, '연비')
            fuel_price = decimal_value(channel.fuel_price, '연료 가격')
            if efficiency <= 0 or fuel_price <= 0:
                raise ValueError('자가 운송에는 0보다 큰 연비와 연료 가격이 필요합니다.')
            fuel_cost = won(distance / efficiency * fuel_price)
            toll = won(decimal_value(channel.toll_cost, '통행료'))
            transport = fuel_cost + toll
        elif channel.transport_mode == 'pickup':
            transport = Decimal(0)
        else:
            raise ValueError('운송 방식이 올바르지 않습니다.')
        expenses = transport + packing + fee + other
        receipts = gross - expenses
        rows.append({
            'channel': channel, 'gross': gross, 'transport': transport, 'fuel': fuel_cost, 'toll': toll,
            'packing': packing, 'commission': fee, 'other': other, 'expenses': expenses,
            'receipts': receipts, 'profit': None if production is None else receipts - production,
        })
    return {'version': CALCULATION_VERSION, 'saleable_kg': saleable, 'production_cost': production, 'rows': rows}
