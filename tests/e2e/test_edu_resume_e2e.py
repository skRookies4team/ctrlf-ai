"""
통합 테스트: 교육 영상 이어보기 자동 재생 기능

테스트 시나리오:
- Q4 인텐트에서 PLAY_VIDEO action 생성 확인
- auto 모드 + 백엔드 꺼짐 → mock 데이터로 PLAY_VIDEO action 반환
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx

from app.clients.personalization_client import PersonalizationClient
from app.models.chat import ChatAction, ChatActionType
from app.models.personalization import PersonalizationFacts


class TestQ4ActionGeneration:
    """Q4 (교육 이어보기) action 생성 통합 테스트"""

    @pytest.mark.asyncio
    async def test_q4_auto_mode_backend_down_generates_action(self):
        """
        Q4: auto 모드 + 백엔드 꺼짐 → mock 데이터로 action 생성 가능

        시나리오:
        1. PersonalizationClient가 auto 모드
        2. 백엔드 연결 실패 (ConnectError)
        3. mock fallback으로 Q4 데이터 반환
        4. mock 데이터에 education_id, video_id 포함 확인
        """
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "auto"
            mock_settings.backend_base_url = "http://localhost:8085"
            mock_settings.BACKEND_API_TOKEN = None

            with patch("app.clients.personalization_client.get_async_http_client") as mock_http:
                mock_http.return_value.post = AsyncMock(
                    side_effect=httpx.ConnectError("Connection refused")
                )

                client = PersonalizationClient()
                facts = await client.resolve_facts("Q4", "test-user-001")

                # 검증 1: 에러 없이 mock fallback 성공
                assert facts.error is None, f"Expected no error, got {facts.error}"

                # 검증 2: items에 영상 정보 포함
                assert facts.items is not None, "items가 없습니다"
                assert len(facts.items) > 0, "items가 비어있습니다"

                # 검증 3: 첫 번째 item에 education_id, video_id 포함 (action 생성 가능)
                last_video = facts.items[0]
                assert "education_id" in last_video or "educationId" in last_video, \
                    f"education_id 필드가 없습니다: {last_video}"
                assert "video_id" in last_video or "videoId" in last_video, \
                    f"video_id 필드가 없습니다: {last_video}"

                # 검증 4: ChatAction 생성 테스트
                education_id = last_video.get("education_id") or last_video.get("educationId")
                video_id = last_video.get("video_id") or last_video.get("videoId")

                action = ChatAction(
                    type=ChatActionType.PLAY_VIDEO,
                    education_id=str(education_id),
                    video_id=str(video_id),
                    resume_position_seconds=last_video.get("resume_position_seconds") or last_video.get("resumePosition"),
                    education_title=last_video.get("education_title") or last_video.get("educationTitle"),
                    video_title=last_video.get("video_title") or last_video.get("videoTitle"),
                    progress_percent=last_video.get("progress_percent") or last_video.get("progressPercent"),
                )

                print(f"\n✅ Q4 action 생성 성공!")
                print(f"   action.type: {action.type}")
                print(f"   action.education_id: {action.education_id}")
                print(f"   action.video_id: {action.video_id}")
                print(f"   action.resume_position_seconds: {action.resume_position_seconds}")
                print(f"   action.education_title: {action.education_title}")

    @pytest.mark.asyncio
    async def test_q4_mock_mode_generates_action(self):
        """
        Q4: mock 모드 → 항상 mock 데이터로 action 생성 가능
        """
        with patch("app.clients.personalization_client.settings") as mock_settings:
            mock_settings.PERSONALIZATION_MODE = "mock"
            mock_settings.backend_base_url = None

            client = PersonalizationClient()
            facts = await client.resolve_facts("Q4", "test-user-002")

            # 검증: mock 데이터 반환
            assert facts.error is None
            assert facts.items is not None
            assert len(facts.items) > 0

            # action 생성 가능 여부 확인
            last_video = facts.items[0]
            education_id = last_video.get("education_id") or last_video.get("educationId")
            video_id = last_video.get("video_id") or last_video.get("videoId")

            assert education_id is not None
            assert video_id is not None

            print(f"\n✅ mock 모드 action 생성 성공!")
            print(f"   education_id: {education_id}")
            print(f"   video_id: {video_id}")


class TestRuleRouterEduResume:
    """RuleRouter EDU_RESUME_CHECK 키워드 매칭 테스트"""

    def test_edu_resume_keywords_match(self):
        """다양한 교육 이어보기 표현이 EDU_RESUME_CHECK으로 매칭되는지 확인"""
        from app.services.rule_router import RuleRouter

        router = RuleRouter()

        # 테스트 케이스: (질문, 예상 sub_intent_id)
        # Note: 일부 표현은 다른 인텐트와 충돌할 수 있음 (EDU_STATUS_CHECK 등)
        test_cases = [
            ("정보보호 교육 이어서 틀어줘", "EDU_RESUME_CHECK"),
            ("보던 교육 다시 틀어줘", "EDU_RESUME_CHECK"),
            ("교육 영상 다시 재생해줘", "EDU_RESUME_CHECK"),
            ("이어보기 해줘", "EDU_RESUME_CHECK"),
        ]

        for query, expected_sub_intent in test_cases:
            result = router.route(query)
            assert result.sub_intent_id == expected_sub_intent, \
                f"Query '{query}': Expected {expected_sub_intent}, got {result.sub_intent_id}"
            print(f"✅ '{query}' → {result.sub_intent_id}")

        print(f"\n✅ 모든 EDU_RESUME_CHECK 키워드 매칭 성공!")
