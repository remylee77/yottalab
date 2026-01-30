"""
중소벤처기업부 비즈인포(기업마당) 사업공고 API 호출 스크립트
지원사업정보 API - 기관별, 분야별 최신 지원사업 공고 조회

※ 인증키 발급: 기업마당(bizinfo.go.kr) > 활용정보 > 정책정보 개방 > API 사용신청
   https://www.bizinfo.go.kr/web/lay1/program/S1T175C174/apiList.do
"""

import json
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

import requests

# API 인증키 (기업마당 API 사용신청 후 발급)
# .env 또는 환경변수 BIZINFO_API_KEY로 설정
API_KEY = os.environ.get("BIZINFO_API_KEY", "")

# API URL
BASE_URL = "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do"


def fetch_bizinfo_announcements(
    api_key: str = None,
    search_cnt: int = 20,
    data_type: str = "json",
    page_index: int = 1,
    page_unit: int = 10,
) -> dict:
    """
    비즈인포 사업공고 최신 목록 조회

    Args:
        api_key: 기업마당 인증키(crtfcKey) - 미입력 시 API_KEY 사용
        search_cnt: 조회 건수 (0 또는 미입력 시 전체, 최대 500)
        data_type: rss(기본) 또는 json
        page_index: 페이지 번호 (페이지네이션 시)
        page_unit: 페이지당 데이터 개수

    Returns:
        API 응답 (JSON 또는 에러 정보)
    """
    key = (api_key or API_KEY or "").strip()
    if not key:
        raise ValueError(
            "API_KEY를 설정해주세요. "
            "환경변수 BIZINFO_API_KEY 또는 fetch_bizinfo_announcements(api_key='...') 사용. "
            "인증키는 기업마당(https://www.bizinfo.go.kr) 정책정보 개방에서 신청하세요."
        )

    params = {
        "crtfcKey": key,
        "dataType": data_type,
        "searchCnt": search_cnt if search_cnt > 0 else "",
        "pageIndex": page_index,
        "pageUnit": page_unit,
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=15)
        response.raise_for_status()

        if data_type == "json":
            return response.json()

        # dataType이 rss(기본)인 경우 XML 반환 → json으로 변환하지 못함
        return {"raw_xml": response.text[:500], "message": "dataType=json으로 요청하세요."}
    except requests.exceptions.RequestException as e:
        return {"error": str(e), "status_code": getattr(e.response, "status_code", None)}
    except json.JSONDecodeError as e:
        return {"error": f"JSON 파싱 실패: {e}", "raw": response.text[:300]}


def main():
    result = fetch_bizinfo_announcements(search_cnt=20, data_type="json")

    if "error" in result:
        print(f"오류: {result['error']}")
        if result.get("status_code"):
            print(f"HTTP 상태코드: {result['status_code']}")
        return

    # JSON 깔끔하게 출력 (indent=2, ensure_ascii=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
