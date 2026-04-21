"""
회사 정보 자동 조회 모듈
도메인 기반으로 로고·회사명·설명을 자동으로 가져옵니다.
- 로고: Clearbit Logo API (무료, 키 불필요)
- 정보: 웹사이트 메타 태그 파싱
"""
import re
import streamlit as st


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_company_info(domain: str) -> dict:
    """도메인에서 회사 로고·이름·설명을 자동 추출 (1시간 캐시)"""
    import requests

    info = {'logo_url': '', 'name': '', 'description': '', 'success': False}
    domain = domain.strip().lower()
    if not domain:
        return info

    # ── 1) 로고: Clearbit → Google Favicon 순서 ──
    for url_candidate in [
        f"https://logo.clearbit.com/{domain}",
        f"https://www.google.com/s2/favicons?domain={domain}&sz=128",
    ]:
        try:
            r = requests.head(url_candidate, timeout=3, allow_redirects=True)
            if r.status_code == 200:
                info['logo_url'] = url_candidate
                break
        except Exception:
            continue

    # ── 2) 웹사이트 메타 태그 ──
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.8',
    }
    for scheme in ['https://', 'http://']:
        try:
            r = requests.get(
                f"{scheme}{domain}", timeout=3,
                headers=headers, allow_redirects=True,
            )
            if r.status_code != 200:
                continue

            # 인코딩 결정: charset 헤더 → meta charset → 자동 감지
            ct = r.headers.get('content-type', '')
            charset_match = re.search(r'charset=([\w\-]+)', ct, re.I)
            if charset_match:
                r.encoding = charset_match.group(1)
            else:
                raw = r.content[:3000]
                meta_cs = re.search(rb'charset=["\']?([\w\-]+)', raw, re.I)
                r.encoding = meta_cs.group(1).decode() if meta_cs else (r.apparent_encoding or 'utf-8')
            html = r.text[:15_000]

            # og:title → <title> 순서로 우선
            og_t = re.search(
                r'<meta[^>]*property=["\']og:site_name["\'][^>]*content=["\']([^"\']+)', html, re.I,
            ) or re.search(
                r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I,
            )
            title_t = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            raw_name = (og_t.group(1) if og_t else (title_t.group(1) if title_t else ''))
            # " - 부가 설명" 등 뒤쪽 제거
            info['name'] = re.split(r'\s*[|\-–—:]\s*', raw_name)[0].strip()

            # 설명
            og_d = re.search(
                r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)', html, re.I,
            )
            meta_d = re.search(
                r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)', html, re.I,
            )
            info['description'] = (
                (og_d.group(1) if og_d else (meta_d.group(1) if meta_d else ''))
                .strip()[:120]
            )

            info['success'] = True
            break
        except Exception:
            continue

    # 로고만이라도 성공하면 success
    if info['logo_url']:
        info['success'] = True

    return info
