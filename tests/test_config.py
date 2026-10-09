import pytest

from babycue_camera.config import InvalidStreamUrl, parse_stream_url


def test_full_url_is_preserved():
    t = parse_stream_url("http://192.168.1.25:8080/video")
    assert t.url == "http://192.168.1.25:8080/video"
    assert (t.host, t.port) == ("192.168.1.25", 8080)
    assert t.is_local_address


def test_bare_ip_gets_defaults():
    assert parse_stream_url("192.168.1.25").url == "http://192.168.1.25:8080/video"
    assert parse_stream_url(" 10.0.0.7:4747 ").url == "http://10.0.0.7:4747/video"


def test_custom_path_and_query_kept():
    t = parse_stream_url("http://192.168.0.5:8081/mjpeg?res=720")
    assert t.url == "http://192.168.0.5:8081/mjpeg?res=720"


def test_explicit_scheme_without_port_uses_scheme_default():
    assert parse_stream_url("http://phone.local/video").port == 80


def test_credentials_are_extracted_and_redacted():
    t = parse_stream_url("http://user:s%40cret@192.168.1.2:8080/video")
    assert t.url == "http://192.168.1.2:8080/video"
    assert (t.username, t.password) == ("user", "s@cret")
    assert "s@cret" not in repr(t)


def test_separate_credentials_take_precedence():
    t = parse_stream_url("http://a:b@192.168.1.2:8080/video", "me", "pw")
    assert (t.username, t.password) == ("me", "pw")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "http://",
        "ftp://192.168.1.2/video",
        "rtsp://192.168.1.2:8554/live",
        "http://192.168.1/video",
        "http://192.168.1.2:99999/video",
        "http://bad host/video",
        "http://exa_mple.com/video",
    ],
)
def test_invalid_urls_rejected(text):
    with pytest.raises(InvalidStreamUrl):
        parse_stream_url(text)


def test_public_address_flagged_as_not_local():
    assert not parse_stream_url("http://8.8.8.8:8080/video").is_local_address
