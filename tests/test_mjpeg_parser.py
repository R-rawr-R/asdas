import pytest

from babycue_camera.stream import MjpegParser, StreamProtocolError
from babycue_camera.stream.mjpeg import boundary_from_content_type

JPEG_A = b"\xff\xd8" + b"A" * 100 + b"\xff\xd9"
JPEG_B = b"\xff\xd8" + b"B" * 50 + b"\xff\xd9"


def part(body, boundary="frame", with_length=True):
    headers = "Content-Type: image/jpeg\r\n"
    if with_length:
        headers += f"Content-Length: {len(body)}\r\n"
    return f"--{boundary}\r\n{headers}\r\n".encode() + body + b"\r\n"


def feed_all(parser, data, chunk=None):
    out = []
    if chunk is None:
        return parser.feed(data)
    for i in range(0, len(data), chunk):
        out.extend(parser.feed(data[i : i + chunk]))
    return out


@pytest.mark.parametrize("chunk", [None, 1, 7, 64])
def test_parses_parts_with_content_length(chunk):
    data = part(JPEG_A) + part(JPEG_B) + part(JPEG_A)
    assert feed_all(MjpegParser("frame"), data, chunk) == [JPEG_A, JPEG_B, JPEG_A]


@pytest.mark.parametrize("chunk", [None, 1, 13])
def test_parses_parts_without_content_length(chunk):
    data = part(JPEG_A, with_length=False) + part(JPEG_B, with_length=False) + b"--frame\r\n"
    assert feed_all(MjpegParser("frame"), data, chunk) == [JPEG_A, JPEG_B]


def test_boundary_with_dashes_in_header():
    # Some servers declare boundary=--frame and also write --frame in the body.
    data = b"--frame\r\nContent-Length: %d\r\n\r\n" % len(JPEG_A) + JPEG_A + b"\r\n"
    assert MjpegParser("--frame").feed(data) == [JPEG_A]


def test_lf_only_headers():
    data = b"--frame\nContent-Type: image/jpeg\nContent-Length: %d\n\n" % len(JPEG_A) + JPEG_A + b"\n"
    assert MjpegParser("frame").feed(data) == [JPEG_A]


def test_leading_garbage_skipped():
    data = b"preamble junk\r\n" + part(JPEG_A)
    assert MjpegParser("frame").feed(data) == [JPEG_A]


def test_marker_scanning_without_boundary():
    data = b"xx" + JPEG_A + b"yy" + JPEG_B
    assert feed_all(MjpegParser(None), data, 5) == [JPEG_A, JPEG_B]


def test_oversized_frame_rejected():
    data = b"--frame\r\nContent-Length: 999999999\r\n\r\n"
    with pytest.raises(StreamProtocolError):
        MjpegParser("frame", max_frame_bytes=1024).feed(data)


def test_runaway_part_without_boundary_rejected():
    parser = MjpegParser("frame", max_frame_bytes=1024)
    parser.feed(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
    with pytest.raises(StreamProtocolError):
        parser.feed(b"\x00" * 4096)


@pytest.mark.parametrize(
    "header,expected",
    [
        ("multipart/x-mixed-replace; boundary=Ba4oTvQMY8ew04N8dcnM", "Ba4oTvQMY8ew04N8dcnM"),
        ('multipart/x-mixed-replace;boundary="--myboundary"', "--myboundary"),
        ("multipart/x-mixed-replace", None),
    ],
)
def test_boundary_from_content_type(header, expected):
    assert boundary_from_content_type(header) == expected
