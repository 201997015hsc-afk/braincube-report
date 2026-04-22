"""
버전 · 배포 정보 모듈
─────────────────────
앱 하단에 표시할 버전 + 배포 시각 + Git 커밋 해시를 제공한다.

우선순위:
  1. 환경변수 APP_VERSION / APP_COMMIT / APP_DEPLOYED_AT (CI에서 주입 가능)
  2. 로컬 .git 정보 읽기 (개발 환경)
  3. 기본값 fallback
"""
import os
import subprocess
from datetime import datetime, timezone
from typing import Optional

# 수동 관리 버전 — 의미 있는 릴리즈 시 증가
__version__ = "1.0.0"


def _read_git_short_hash() -> Optional[str]:
    """로컬 .git에서 짧은 커밋 해시 읽기. 실패 시 None."""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=base, capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _read_git_commit_date() -> Optional[str]:
    """마지막 커밋 시각 ISO 문자열. 실패 시 None."""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cI"],
            cwd=base, capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def get_version() -> str:
    """앱 버전 문자열 반환. 환경변수 우선, 없으면 모듈 상수."""
    return os.environ.get("APP_VERSION") or __version__


def get_commit_hash() -> Optional[str]:
    """짧은 커밋 해시 반환 (8자). 실패 시 None."""
    env = os.environ.get("APP_COMMIT")
    if env:
        return env[:8]
    git_hash = _read_git_short_hash()
    return git_hash[:8] if git_hash else None


def get_deployed_at() -> Optional[str]:
    """배포 시각 ISO 문자열. 환경변수 → git commit → None."""
    env = os.environ.get("APP_DEPLOYED_AT")
    if env:
        return env
    return _read_git_commit_date()


def format_deployed_relative() -> str:
    """'2시간 전', '어제', '3일 전' 같은 상대 시각 문자열.

    배포 시각을 알 수 없으면 빈 문자열 반환.
    """
    iso = get_deployed_at()
    if not iso:
        return ""
    try:
        # ISO 파싱 (타임존 포함/미포함 모두 지원)
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        secs = int(delta.total_seconds())
        if secs < 0:
            return "방금"
        if secs < 60:
            return f"{secs}초 전"
        mins = secs // 60
        if mins < 60:
            return f"{mins}분 전"
        hours = mins // 60
        if hours < 24:
            return f"{hours}시간 전"
        days = hours // 24
        if days < 7:
            return f"{days}일 전"
        weeks = days // 7
        if weeks < 5:
            return f"{weeks}주 전"
        months = days // 30
        if months < 12:
            return f"{months}개월 전"
        return f"{days // 365}년 전"
    except Exception:
        return ""


def get_footer_text() -> str:
    """푸터용 한 줄 문자열. 'v1.0.0 · a1b2c3d · 2시간 전 배포'"""
    parts = [f"v{get_version()}"]
    commit = get_commit_hash()
    if commit:
        parts.append(commit)
    rel = format_deployed_relative()
    if rel:
        parts.append(f"{rel} 배포")
    return " · ".join(parts)
