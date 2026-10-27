import requests
import logging
from datetime import datetime, timedelta
from django.utils import timezone
from config.environment import required_api_setting

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 상수
# ─────────────────────────────────────────

SHORT_FCST_URL = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
MID_LAND_URL   = "http://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst"   # 중기육상예보 (강수)
MID_TEMP_URL   = "http://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa"         # 중기기온예보

# 주요 지역 nx, ny 격자 (단기예보용)
# 출처: 기상청 단기예보 조회서비스 활용가이드 엑셀
NX_NY_TABLE = {
    "서울":   (60, 127),
    "인천":   (55, 124),
    "수원":   (60, 121),
    "춘천":   (73, 134),
    "강릉":   (92, 131),
    "청주":   (69, 107),
    "대전":   (67, 100),
    "전주":   (63,  89),
    "광주":   (58,  74),
    "대구":   (89,  90),
    "부산":   (98,  76),
    "울산":   (102,  84),
    "창원":   (91,  77),
    "제주":   (52,  38),
    "완주":   (63,  90),
    "익산":   (60,  91),
    "김제":   (59,  88),
}

# 중기예보 지역코드 (육상예보용)
MID_LAND_CODE = {
    "서울":   "11B00000",
    "인천":   "11B00000",
    "경기":   "11B00000",
    "춘천":   "11D10000",  # 강원영서
    "강릉":   "11D20000",  # 강원영동
    "청주":   "11C10000",  # 충북
    "대전":   "11C20000",  # 충남
    "전주":   "11F10000",  # 전북
    "광주":   "11F20000",  # 전남
    "대구":   "11H10000",  # 경북
    "부산":   "11H20000",  # 경남
    "제주":   "11G00000",
    "완주":   "11F10000",  # 전북
    "익산":   "11F10000",
    "김제":   "11F10000",
}

# 중기예보 기온 지역코드
MID_TEMP_CODE = {
    "서울":   "11B10101",
    "인천":   "11B20201",
    "수원":   "11B20601",
    "춘천":   "11D10301",
    "강릉":   "11D20501",
    "청주":   "11C10301",
    "대전":   "11C20401",
    "전주":   "11F10201",
    "광주":   "11F20501",
    "대구":   "11H10701",
    "부산":   "11H20201",
    "울산":   "11H20101",
    "제주":   "11G00201",
    "완주":   "11F10201",  # 전주 기준
    "익산":   "11F10201",
    "김제":   "11F10201",
}


# ─────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────

def get_base_time():
    """
    단기예보 base_time 계산
    발표 시각: 02, 05, 08, 11, 14, 17, 20, 23시
    API는 발표 후 10분 뒤부터 조회 가능
    """
    now = datetime.now()
    base_times = [2, 5, 8, 11, 14, 17, 20, 23]

    # 현재 시각 기준 가장 최근 발표 시각 찾기
    current_hour = now.hour
    base_hour = 23  # 기본값

    for t in reversed(base_times):
        if current_hour >= t:
            # 발표 후 10분 지났는지 확인
            if current_hour == t and now.minute < 10:
                continue
            base_hour = t
            break

    base_date = now.strftime('%Y%m%d')
    # 자정~02:10 사이면 전날 23시 발표 기준
    if current_hour < 2 or (current_hour == 2 and now.minute < 10):
        yesterday = now - timedelta(days=1)
        base_date = yesterday.strftime('%Y%m%d')
        base_hour = 23

    return base_date, f"{base_hour:02d}00"


def get_mid_base_time():
    """
    중기예보 base_time 계산
    발표 시각: 06시, 18시 (하루 2회)
    """
    now = datetime.now()
    if now.hour >= 18:
        return now.strftime('%Y%m%d') + "1800"
    elif now.hour >= 6:
        return now.strftime('%Y%m%d') + "0600"
    else:
        yesterday = now - timedelta(days=1)
        return yesterday.strftime('%Y%m%d') + "1800"


# ─────────────────────────────────────────
# 단기예보 API
# ─────────────────────────────────────────

def fetch_short_forecast(nx: int, ny: int) -> list[dict]:
    """
    단기예보 조회 (1~3일, 3시간 간격)

    Returns:
        list of {date, hour, TMP, POP, PCP, REH, WSD, SKY}
    """
    base_date, base_time = get_base_time()

    params = {
        'serviceKey': required_api_setting('KMA_API_KEY'),
        'numOfRows':  1000,
        'pageNo':     1,
        'dataType':   'JSON',
        'base_date':  base_date,
        'base_time':  base_time,
        'nx':         nx,
        'ny':         ny,
    }

    try:
        resp = requests.get(SHORT_FCST_URL, params=params, timeout=30)
        data = resp.json()

        items = data['response']['body']['items']['item']
        logger.info(f"단기예보 {len(items)}건 수신 (base: {base_date} {base_time})")

        # 날짜/시간별로 정리
        result = {}
        for item in items:
            key = (item['fcstDate'], item['fcstTime'])
            if key not in result:
                result[key] = {'date': item['fcstDate'], 'hour': item['fcstTime']}
            result[key][item['category']] = item['fcstValue']

        return list(result.values())

    except Exception as e:
        logger.error('단기예보 조회 실패 (%s). 요청 URL·인증키는 기록하지 않습니다.', type(e).__name__)
        return []


def parse_short_forecast(raw_items: list[dict]) -> list[dict]:
    """
    단기예보 원시 데이터를 일별로 집계

    Returns:
        list of {date, temp_max, temp_min, rain_prob, rain_mm, humidity, wind_speed, sky}
    """
    daily = {}

    for item in raw_items:
        date = item['date']
        if date not in daily:
            daily[date] = {
                'date':       date,
                'temps':      [],
                'rain_probs': [],
                'rains':      [],
                'humids':     [],
                'winds':      [],
                'skies':      [],
            }

        # 기온
        if 'TMP' in item:
            try:
                daily[date]['temps'].append(float(item['TMP']))
            except (ValueError, TypeError):
                pass

        # 강수확률
        if 'POP' in item:
            try:
                daily[date]['rain_probs'].append(float(item['POP']))
            except (ValueError, TypeError):
                pass

        # 강수량 (1mm 미만, 강수없음 처리)
        if 'PCP' in item:
            val = item['PCP']
            if val in ('강수없음', '0'):
                daily[date]['rains'].append(0.0)
            elif '1mm 미만' in str(val):
                daily[date]['rains'].append(0.5)
            else:
                try:
                    daily[date]['rains'].append(float(str(val).replace('mm', '').strip()))
                except (ValueError, TypeError):
                    pass

        # 습도
        if 'REH' in item:
            try:
                daily[date]['humids'].append(float(item['REH']))
            except (ValueError, TypeError):
                pass

        # 풍속
        if 'WSD' in item:
            try:
                daily[date]['winds'].append(float(item['WSD']))
            except (ValueError, TypeError):
                pass

        # 하늘상태
        if 'SKY' in item:
            try:
                daily[date]['skies'].append(int(item['SKY']))
            except (ValueError, TypeError):
                pass

    # 일별 집계
    parsed = []
    for date, d in sorted(daily.items()):
        parsed.append({
            'date':       datetime.strptime(date, '%Y%m%d').date(),
            'temp_max':   max(d['temps'])         if d['temps']      else None,
            'temp_min':   min(d['temps'])         if d['temps']      else None,
            'rain_prob':  max(d['rain_probs'])    if d['rain_probs'] else None,
            'rain_mm':    sum(d['rains'])         if d['rains']      else 0.0,
            'humidity':   sum(d['humids']) / len(d['humids']) if d['humids'] else None,
            'wind_speed': max(d['winds'])         if d['winds']      else None,
            'sky':        max(d['skies'])         if d['skies']      else None,
            'source':     'short',
        })

    return parsed


# ─────────────────────────────────────────
# 중기예보 API
# ─────────────────────────────────────────

def fetch_mid_forecast(region: str) -> list[dict]:
    """
    중기예보 조회 (4~11일)
    육상예보(강수)와 기온예보를 각각 호출 후 합침

    Returns:
        list of {date, temp_max, temp_min, rain_prob, sky, source}
    """
    tmfc = get_mid_base_time()
    land_code = MID_LAND_CODE.get(region)
    temp_code = MID_TEMP_CODE.get(region)

    if not land_code or not temp_code:
        logger.error(f"지원하지 않는 지역: {region}")
        return []

    # 1. 중기육상예보 (강수확률, 하늘상태)
    mid_key = required_api_setting('KMA_MID_API_KEY')
    land_data = {}
    try:
        resp = requests.get(MID_LAND_URL, params={
            'serviceKey': mid_key,
            'numOfRows':  10,
            'pageNo':     1,
            'dataType':   'JSON',
            'regId':      land_code,
            'tmFc':       tmfc,
        }, timeout=30)
        item = resp.json()['response']['body']['items']['item'][0]

        sky_map = {'맑음': 1, '구름조금': 2, '구름많음': 3, '흐림': 4, '흐리고 비': 4}

        # 4~7일: Am/Pm 두 파트로 파싱
        for day in range(4, 8):
            for part in ['Am', 'Pm']:
                key = f"rnSt{day}{part}"
                sky_key = f"wf{day}{part}"
                if key in item:
                    if day not in land_data:
                        land_data[day] = {'rain_probs': [], 'skies': []}
                    try:
                        land_data[day]['rain_probs'].append(float(item[key]))
                    except (ValueError, TypeError):
                        pass
                    if sky_key in item:
                        # 맑음=1, 구름조금=2, 구름많음=3, 흐림=4
                        land_data[day]['skies'].append(sky_map.get(item[sky_key], 3))

        # 8~10일: 단일 파트로 파싱 (Am/Pm 없음)
        for day in range(8, 11):
            key = f"rnSt{day}"
            sky_key = f"wf{day}"
            if key in item:
                if day not in land_data:
                    land_data[day] = {'rain_probs': [], 'skies': []}
                try:
                    land_data[day]['rain_probs'].append(float(item[key]))
                except (ValueError, TypeError):
                    pass
                if sky_key in item:
                    land_data[day]['skies'].append(sky_map.get(item[sky_key], 3))

        logger.info(f"중기육상예보 수신: {region}")

    except Exception as e:
        logger.error('중기육상예보 조회 실패 (%s). 요청 URL·인증키는 기록하지 않습니다.', type(e).__name__)

    # 2. 중기기온예보
    temp_data = {}
    try:
        resp = requests.get(MID_TEMP_URL, params={
            'serviceKey': mid_key,
            'numOfRows':  10,
            'pageNo':     1,
            'dataType':   'JSON',
            'regId':      temp_code,
            'tmFc':       tmfc,
        }, timeout=30)
        item = resp.json()['response']['body']['items']['item'][0]

        for day in range(4, 12):
            max_key = f"taMax{day}"
            min_key = f"taMin{day}"
            if max_key in item:
                temp_data[day] = {
                    'temp_max': float(item[max_key]) if item[max_key] else None,
                    'temp_min': float(item[min_key]) if item[min_key] else None,
                }

        logger.info(f"중기기온예보 수신: {region}")

    except Exception as e:
        logger.error('중기기온예보 조회 실패 (%s). 요청 URL·인증키는 기록하지 않습니다.', type(e).__name__)

    # 3. 합치기
    today = datetime.now().date()
    result = []
    for day in range(4, 12):
        target_date = today + timedelta(days=day)
        land = land_data.get(day, {})
        temp = temp_data.get(day, {})

        rain_probs = land.get('rain_probs', [])
        skies = land.get('skies', [])

        result.append({
            'date':       target_date,
            'temp_max':   temp.get('temp_max'),
            'temp_min':   temp.get('temp_min'),
            'rain_prob':  max(rain_probs) if rain_probs else None,
            'rain_mm':    None,  # 중기예보는 강수량 없음
            'humidity':   None,  # 중기예보는 습도 없음
            'wind_speed': None,  # 중기예보는 풍속 없음
            'sky':        max(skies) if skies else None,
            'source':     'mid',
        })

    return result


# ─────────────────────────────────────────
# 통합 예보 조회 + DB 저장
# ─────────────────────────────────────────

def fetch_and_save_forecast(farm) -> dict:
    """
    단기 + 중기 예보를 조회해서 WeatherForecast DB에 저장

    Args:
        farm: Farm 모델 인스턴스

    Returns:
        {'short': N건, 'mid': M건, 'status': 'ok'}
    """
    from weather.models import WeatherForecast

    # 필수 설정을 모두 확인한 뒤 조회·저장을 시작한다.
    required_api_setting('KMA_API_KEY')
    required_api_setting('KMA_MID_API_KEY')

    short_saved = 0
    mid_saved = 0

    # 1. 단기예보
    raw = fetch_short_forecast(farm.nx, farm.ny)
    short_items = parse_short_forecast(raw)

    for item in short_items:
        obj, created = WeatherForecast.objects.update_or_create(
            farm=farm,
            forecast_date=item['date'],
            source='short',
            defaults={
                'temp_max':      item.get('temp_max'),
                'temp_min':      item.get('temp_min'),
                'rain_prob':     item.get('rain_prob'),
                'rain_mm':       item.get('rain_mm'),
                'humidity':      item.get('humidity'),
                'wind_speed':    item.get('wind_speed'),
                'sky_condition': item.get('sky'),
            }
        )
        short_saved += 1   # 신규/업데이트 모두 카운트

    # 2. 중기예보 (지역명은 farm.region_code에서 가져옴)
    region = farm.region_code  # 예: "전주", "완주" 등
    mid_items = fetch_mid_forecast(region)

    for item in mid_items:
        obj, created = WeatherForecast.objects.update_or_create(
            farm=farm,
            forecast_date=item['date'],
            source='mid',
            defaults={
                'temp_max':      item.get('temp_max'),
                'temp_min':      item.get('temp_min'),
                'rain_prob':     item.get('rain_prob'),
                'rain_mm':       item.get('rain_mm'),
                'humidity':      item.get('humidity'),
                'wind_speed':    item.get('wind_speed'),
                'sky_condition': item.get('sky'),
            }
        )
        mid_saved += 1   # 신규/업데이트 모두 카운트

    logger.info(f"예보 저장 완료: 단기 {short_saved}건, 중기 {mid_saved}건")
    return {
        'status': 'ok',
        'short':  short_saved,
        'mid':    mid_saved,
        'total':  short_saved + mid_saved,
    }
