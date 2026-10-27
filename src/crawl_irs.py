# library import
import requests
import pandas as pd
import re
import time

from bs4 import BeautifulSoup
from urllib.parse import urljoin
from io import StringIO

import os
from tqdm.auto import tqdm

# result example
# ======================================================================
#         Date Tenor  Receive     Mid     Pay
# 0 2026-10-01    1Y   3.6975  3.6875  3.6775
# 1 2026-10-01    2Y   3.9750  3.9650  3.9550
# 2 2026-10-01    3Y   4.0875  4.0775  4.0675
# 3 2026-10-01    4Y   4.1475  4.1375  4.1275
# 4 2026-10-01    5Y   4.1875  4.1775  4.1675
#                       Date     Receive         Mid         Pay
# count                  190  190.000000  190.000000  190.000000
# mean   2026-10-14 00:00:00    3.940500    3.930500    3.920500
# min    2026-10-01 00:00:00    3.540000    3.530000    3.520000
# 25%    2026-10-07 00:00:00    3.823125    3.813125    3.803125
# 50%    2026-10-14 00:00:00    3.936250    3.926250    3.916250
# 75%    2026-10-21 00:00:00    4.079375    4.069375    4.059375
# max    2026-10-27 00:00:00    4.272500    4.262500    4.252500
# std                    NaN    0.176939    0.176939    0.176939
# ======================================================================


# set configs
START = '2026-10-01'
END = '2026-10-27'

SAVE_PATH = 'output/IRS_data.csv'

URLS = [
    "http://www.smbs.biz/Exchange/IRS.jsp",
    "https://www.smbs.biz/Exchange/IRS.jsp",
    "https://www.smbs.co.kr/Exchange/IRS.jsp"
]

CALL_RE = re.compile(
    r"""d[1-5]\s*\(\s*(['"])(.*?)\1\s*\)\s*;?""",
    re.S
)

HEX_RE = re.compile(
    r"%u_[A-Za-z]([0-9A-Fa-f]{4})|%_[A-Za-z]([0-9A-Fa-f]{2})"
)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,*/*"
})

# functions
def decode_smbs(value : str) -> str :
    """
    SMBS로부터 받은 데이터를 디코딩하는 함수
    :param value: SMBS로부터 수집한 원본 텍스트 데이터
    :return: 정제한 텍스트 데이터 (str)
    """
    if pd.isna(value):
        return value

    def replace_call(m):
        encoded = m.group(2)

        return HEX_RE.sub(
            lambda x: chr(int(x.group(1) or x.group(2), 16)),
            encoded
        )

    return CALL_RE.sub(replace_call, str(value)).strip()

def parse_irs(r : str) -> pd.DataFrame :
    """
    SMBS로부터 수집한 금리 데이터를 pd.DataFrame형태로 변환합니다.
    :param r:
    :return:
    """
    html = r.content.decode("cp949", errors="replace")
    tables = pd.read_html(StringIO(html))

    for table in tables:
        df = table.copy()

        # 컬럼명 복원
        df.columns = [decode_smbs(c) for c in df.columns]

        # IRS 데이터 테이블 자동 식별
        if not {"Receive", "Mid", "Pay"}.issubset(df.columns):
            continue

        # 첫 번째 열을 Tenor로 지정
        df = df.rename(columns={df.columns[0]: "Tenor"})

        # 데이터 복원
        df = df.apply(lambda col: col.map(decode_smbs))

        # 만기 자동 인식 (1Y, 12Y, 20Y, 30Y, 6M 등)
        df["Tenor"] = df["Tenor"].str.strip()

        df = df[
            df["Tenor"].str.fullmatch(r"\d+\s*[DWMY]", na=False)
        ].copy()

        # 금리 컬럼 변환
        for col in df.columns.drop("Tenor"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df.reset_index(drop=True)

    raise ValueError("IRS 금리 테이블을 찾지 못했습니다.")

def fetch_irs_day(date : str, response, form) -> pd.DataFrame :
    """
    IRS Data를 수집하는 함수
    :param date:
    :return:
    """
    dt = pd.Timestamp(date)
    day = dt.strftime("%Y.%m.%d")

    payload = {
        inp["name"]: inp.get("value", "")
        for inp in form.select("input[name]")
        if inp.get("type", "text").lower()
           not in ("submit", "button", "reset", "image", "file")
    }

    # 모든 날짜 필드를 동일한 조회일로 설정
    for name in ("StrSchFull", "StrSchFull2", "StrSchFull3"):
        payload[name] = day

    for prefix in ("StrSch_s", "StrSch_e", "StrSch_"):
        payload[prefix + "Year"] = dt.strftime("%Y")
        payload[prefix + "Month"] = dt.strftime("%m")
        payload[prefix + "Day"] = dt.strftime("%d")

    url = urljoin(response.url, form.get("action") or response.url)
    r = session.post(url, data=payload, timeout=30)
    r.raise_for_status()

    # 입력값이 아니라 화면에 표시된 실제 기준일을 확인
    html = r.content.decode("cp949", errors="replace")
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)

    matches = re.findall(
        r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일",
        text
    )

    dates = sorted(set(
        f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        for y, m, d in matches
    ))

    requested = dt.strftime("%Y-%m-%d")

    if requested not in dates:
        print(f"[검증 실패] 요청일={requested}, 표시일={dates}")
        return None

    df = parse_irs(r)
    df.insert(0, "Date", dt)

    # print(f"[성공] {requested} | 만기 {len(df)}개")
    return df

if __name__ == "__main__":
    dates = [
        "2009-01-02",
        "2009-01-05",
        "2026-10-13"
    ]

    response = None

    for url in URLS:
        try:
            r = session.get(url, timeout=20)
            print(f"[접속] {r.status_code} | {r.url}")

            if r.status_code == 200:
                response = r
                break

        except requests.RequestException as e:
            print(f"[오류] {url}: {str(e)[:150]}")

    if response is None:
        raise RuntimeError("사이트 접속 실패: Colab 네트워크에서 확인 필요")

    soup = BeautifulSoup(response.text, "html.parser")
    form = soup.find("form")


    # 기존 수집 데이터 불러오기
    if os.path.exists(SAVE_PATH):
        result = pd.read_csv(SAVE_PATH)
        completed = set(result["Date"].astype(str))
        print(f"[RESUME] {len(completed)}일 수집 완료")
    else:
        result = pd.DataFrame()
        completed = set()

    dates = pd.bdate_range(START, END)

    failed = []

    records = []
    errors = []

    for i, dt in enumerate(tqdm(dates, desc='Crawl IRS Data', unit='date'), 1):
        date = dt.strftime("%Y-%m-%d")

        if date in completed:
            continue

        try:
            df = fetch_irs_day(date, response, form)

            if df is None or df.empty:
                failed.append(date)
                continue

            result = pd.concat(
                [result, df],
                ignore_index=True
            )

            completed.add(date)

            # if i <= 5 or i % 10 == 0:
            #     tqdm.write(
            #         f"[{i}/{len(dates)}] "
            #         f"SUCCESS | {date} "
            #         f"| 성공: {len(records)} "
            #         f"| 실패: {len(errors)}"
            #     )

            if len(completed) % 50 == 0:
                result.to_csv(
                    SAVE_PATH,
                    index=False,
                )

            time.sleep(0.5)

        except Exception as e:
            failed.append(date)
            print(f"[ERROR] {date}: {e}")
            time.sleep(2)

    result.to_csv(
        SAVE_PATH,
        index = False,
        encoding = 'cp949'
    )

    print(result.head())
    print(result.describe())