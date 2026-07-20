from app.driver_portraits import parse_official_driver_portraits


def test_parse_official_driver_portraits_uses_full_length_assets():
    page = """
    <img src="https://media.formula1.com/image/upload/c_lfill,w_440/q_auto/v1/common/f1/2026/audi/gabbor01/2026audigabbor01right.webp" alt="Gabriel Bortoleto" role="presentation"/>
    <img src="https://media.formula1.com/image/upload/c_lfill,w_440/q_auto/v1/common/f1/2026/audi/gabbor01/2026audigabbor01right.webp" alt="Gabriel Bortoleto" role="presentation"/>
    """

    assert parse_official_driver_portraits(page) == [{
        "full_name": "Gabriel Bortoleto",
        "image_url": "https://media.formula1.com/image/upload/c_lfill,w_440/q_auto/v1/common/f1/2026/audi/gabbor01/2026audigabbor01right.webp",
        "season": 2026,
        "source_url": "https://www.formula1.com/en/drivers",
    }]
