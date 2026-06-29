"""ComPass Intent Engine 유사어 사전.

Intent 분류 전 단계에서 사용자 표현을 공식 용어로 정규화한다.
"""

from __future__ import annotations

import re


SYNONYM_GROUPS: dict[str, list[str]] = {
    "공지": ["공지사항", "최근공지", "최신공지", "새공지", "소식", "학과공지"],
    "교수진": ["교수님", "교수소개", "교수 소개", "교수정보", "담당교수", "담당 교수", "학과교수"],
    "교육과정": ["교과과정", "커리큘럼", "전공과목", "교과목", "수업구성"],
    "학과일정": ["학사일정", "행사일정", "일정", "오티", "OT", "오리엔테이션"],
    "졸업": ["졸업요건", "졸업조건", "졸업학점"],
    "편입": ["신편입", "신편입생", "편입생", "3학년편입"],
    "시험": ["중간고사", "기말고사", "출석시험", "온라인시험", "평가"],
    "장학금": ["국가장학금", "성적장학금", "장학혜택"],
    "컴퓨터과학과": ["컴퓨터 과학과", "컴공", "컴과"],
}


def synonym_replacements() -> dict[str, str]:
    replacements: dict[str, str] = {}
    for canonical, values in SYNONYM_GROUPS.items():
        for value in values:
            replacements[value] = canonical
    return replacements


def apply_synonyms(text: str) -> str:
    """긴 유사어부터 공식 용어로 치환한다."""
    normalized = text or ""
    replacements = synonym_replacements()
    for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        normalized = re.sub(re.escape(source), target, normalized, flags=re.IGNORECASE)
    return normalized
