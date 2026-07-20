from app.config import Settings


def test_frontend_origins_include_render_hostname():
    settings = Settings(
        frontend_origin="http://localhost:5173/",
        frontend_hostname="race-data-web.onrender.com/",
    )

    assert settings.frontend_origins == [
        "http://localhost:5173",
        "https://race-data-web.onrender.com",
    ]


def test_frontend_origins_do_not_duplicate_origin():
    settings = Settings(
        frontend_origin="https://race-data-web.onrender.com",
        frontend_hostname="race-data-web.onrender.com",
    )

    assert settings.frontend_origins == ["https://race-data-web.onrender.com"]
