from search_index import SearchIndex


def test_course_catalog_is_built_and_longest_alias_is_detected(tmp_path) -> None:
    index = SearchIndex(tmp_path / "catalog.json")
    result = index.rebuild(
        [
            {
                "page_id": "course-page",
                "title": "파이썬프로그래밍기초",
                "category": "교과정보 > 교과목안내 > 과목상세",
                "document_type": "과목상세",
                "source_url": "https://cs.knou.ac.kr/cs1/4791/subview.do#course-34174",
                "normalized_items": [
                    {
                        "course_name": "파이썬프로그래밍기초",
                        "overview": "파이썬 기초 문법 학습",
                    }
                ],
            }
        ]
    )

    detected = index.detect_course("파이썬 수업 난이도는?")

    assert result["courses"] == 1
    assert detected["course_name"] == "파이썬프로그래밍기초"
    assert detected["document_ids"] == ["course-page"]
    assert detected["document_types"] == ["과목상세"]
