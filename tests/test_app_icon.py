"""Tests for the branded application icon."""

from PyQt6.QtCore import QSize

from tests.qt_helpers import qt_app


def test_branded_icon_asset_is_present_and_renderable():
    """The app icon should load into a non-empty Qt icon at desktop sizes."""
    qt_app()
    from kgb_srs.app_icon import APP_ICON_PATH, get_app_icon

    assert APP_ICON_PATH.is_file()

    icon = get_app_icon()
    assert not icon.isNull()
    assert not icon.pixmap(QSize(64, 64)).isNull()


def test_configure_application_sets_branded_identity():
    """Launch configuration should replace Qt's generic application icon."""
    application = qt_app()
    from kgb_srs.app_icon import APPLICATION_NAME, configure_application, get_app_icon

    configure_application(application)

    assert application.applicationName() == APPLICATION_NAME
    assert application.applicationDisplayName() == APPLICATION_NAME
    configured_image = application.windowIcon().pixmap(QSize(64, 64)).toImage()
    expected_image = get_app_icon().pixmap(QSize(64, 64)).toImage()
    assert configured_image == expected_image
