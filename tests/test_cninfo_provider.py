from __future__ import annotations

from urllib.parse import parse_qs
from unittest.mock import patch

from astock_data.providers.cninfo import CninfoProvider


class FakeResponse:
    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._payload


def test_announcements_resolves_orgid_and_normalizes_rows():
    provider = CninfoProvider()
    responses = [
        FakeResponse('{"stockList":[{"code":"601318","orgId":"9900002221"}]}'),
        FakeResponse(
            """
            {
              "announcements": [
                {
                  "announcementTitle": "年度报告",
                  "announcementTypeName": "定期报告",
                  "announcementTime": 1781049600000,
                  "announcementId": "12345"
                }
              ]
            }
            """
        ),
    ]

    with patch("astock_data.providers.cninfo.urlopen", side_effect=responses) as urlopen_mock:
        result = provider.get_announcements(
            code="SH601318",
            start_date="2026-06-01",
            end_date="2026-06-10",
            limit=1,
        )

    assert result.status == "ok"
    assert result.coverage["filtered_code"] == "601318"
    assert result.data == [
        {
            "code": "601318",
            "title": "年度报告",
            "type": "定期报告",
            "date": "2026-06-10",
            "announcement_id": "12345",
            "url": "https://www.cninfo.com.cn/new/disclosure/detail?annoId=12345",
        }
    ]
    posted_body = urlopen_mock.call_args_list[1].args[0].data.decode("utf-8")
    posted = parse_qs(posted_body)
    assert posted["stock"] == ["601318,9900002221"]
    assert posted["seDate"] == ["2026-06-01~2026-06-10"]


def test_announcements_falls_back_to_legacy_orgid_when_map_missing():
    provider = CninfoProvider()
    responses = [
        FakeResponse('{"stockList":[]}'),
        FakeResponse('{"announcements":[]}'),
    ]

    with patch("astock_data.providers.cninfo.urlopen", side_effect=responses) as urlopen_mock:
        result = provider.get_announcements(code="600519", limit=1)

    assert result.status == "empty"
    posted_body = urlopen_mock.call_args_list[1].args[0].data.decode("utf-8")
    assert parse_qs(posted_body)["stock"] == ["600519,gssh0600519"]


def test_announcements_preserves_single_sided_date_filters():
    provider = CninfoProvider()
    responses = [
        FakeResponse('{"stockList":[{"code":"600519","orgId":"gssh0600519"}]}'),
        FakeResponse('{"announcements":[]}'),
        FakeResponse('{"announcements":[]}'),
    ]

    with patch("astock_data.providers.cninfo.urlopen", side_effect=responses) as urlopen_mock:
        provider.get_announcements(code="600519", start_date="2026-06-01", limit=1)
        provider.get_announcements(code="600519", end_date="2026-06-10", limit=1)

    start_only_body = urlopen_mock.call_args_list[1].args[0].data.decode("utf-8")
    end_only_body = urlopen_mock.call_args_list[2].args[0].data.decode("utf-8")
    assert parse_qs(start_only_body)["seDate"] == ["2026-06-01~"]
    assert parse_qs(end_only_body)["seDate"] == ["~2026-06-10"]


def test_cninfo_http_exception_returns_structured_unavailable():
    provider = CninfoProvider()

    with patch("astock_data.providers.cninfo.urlopen", side_effect=OSError("network down")):
        result = provider.get_announcements(code="600519")

    assert result.status == "unavailable"
    assert result.data == []
    assert result.coverage["coverage_ratio"] == 0.0
    assert "network down" in result.coverage["warnings"][0]
