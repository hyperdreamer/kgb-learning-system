"""Regression tests for main-window composition and layout."""

import os

import pytest

from .qt_helpers import qt_app as _qt_app


@pytest.fixture(autouse=True)
def _dispose_top_level_widgets():
    """Keep root-local QSS from one Qt test out of the next test."""
    yield
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class TestNoSyncHTTPInMainWindow:
    """Main window must not contain processEvents or synchronous HTTP calls."""

    def test_no_process_events(self):
        main_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "kgb_srs", "main_window.py"
        )
        with open(main_path, "r") as f:
            content = f.read()
        assert "processEvents" not in content, "processEvents found in main_window.py"
        assert "_make_http_call" not in content, (
            "_make_http_call found in main_window.py"
        )
        assert "urllib.request.urlopen" not in content, (
            "urllib.request.urlopen found in main_window.py"
        )


class TestAddEntryButtonLabel:
    """The top-toolbar button for adding items must have label 'Add Entry'."""

    def test_add_entry_button_label_is_add_entry(self):
        """The toolbar button stored as add_entry_btn has visible text
        'Add Entry' after stripping leading whitespace."""
        _qt_app()
        from PyQt6.QtWidgets import QPushButton
        from kgb_srs.main_window import BarskyApp

        win = BarskyApp()

        # Button must be stored as a stable attribute on the window
        assert hasattr(win, "add_entry_btn"), (
            "BarskyApp must store the add-entry toolbar button as "
            "self.add_entry_btn for stable testability"
        )
        btn = win.add_entry_btn
        assert isinstance(btn, QPushButton)

        # Visible label: strip leading whitespace/mnemonic; assert exact text
        visible_text = btn.text().strip()
        assert visible_text == "Add Entry", (
            f"Expected toolbar button label 'Add Entry', got '{visible_text}'"
        )

        win.close()


class TestMenuSubmenuSpacing:
    """Database-selection QMenu items must have right padding so text
    never overlaps the submenu arrow indicator (">")."""

    def test_menu_stylesheet_has_readable_item_padding(self):
        """Generated menu CSS keeps menu items readable around submenu arrows."""
        from kgb_srs.ui_theme import menu_stylesheet

        css = menu_stylesheet("Arial", 14)

        assert "QMenu::item" in css
        assert "padding: 6px 24px 6px 24px;" in css

    def test_menu_stylesheet_has_readable_vertical_item_padding(self):
        """Generated menu CSS gives QMenu items at least 6 px vertically."""
        import re

        from kgb_srs.ui_theme import menu_stylesheet

        css = menu_stylesheet("Arial", 14)
        item_rule = re.search(r"QMenu::item\s*\{(?P<body>.*?)\}", css, re.DOTALL)
        assert item_rule is not None
        padding = re.search(
            r"padding:\s*(\d+)px\s+(\d+)px\s+(\d+)px\s+(\d+)px",
            item_rule.group("body"),
        )
        assert padding is not None
        assert int(padding.group(1)) >= 6
        assert int(padding.group(3)) >= 6

    def test_root_menu_uses_default_generated_stylesheet_without_settings_attribute(
        self, tmp_path, monkeypatch
    ):
        """A generic owner gets default generated CSS without a settings mapping."""
        _qt_app()

        from PyQt6.QtWidgets import QMenu

        from kgb_srs.catalog import DatabaseType
        from kgb_srs.main_window import BarskyApp
        from kgb_srs.ui_theme import menu_stylesheet

        db_dir = tmp_path / "db" / "Language-based" / "Sentence-based"
        db_dir.mkdir(parents=True)
        alpha = db_dir / "Alpha_barsky.db"
        zulu = db_dir / "Zulu_barsky.db"
        alpha.write_text("")
        zulu.write_text("")

        monkeypatch.setattr(
            "kgb_srs.main_window.find_databases",
            lambda *args, **kwargs: [("Zulu", str(zulu)), ("Alpha", str(alpha))],
        )
        monkeypatch.setattr(
            "kgb_srs.main_window._open_and_infer_type",
            lambda path: DatabaseType.LANGUAGE_SENTENCE,
        )

        class FakeApp:
            current_db_path = str(zulu)

        menu = QMenu()
        BarskyApp.build_db_menu(FakeApp(), menu)

        def leaf_actions(current_menu):
            leaves = []
            for action in current_menu.actions():
                if action.menu():
                    leaves.extend(leaf_actions(action.menu()))
                elif action.data():
                    leaves.append(action)
            return leaves

        assert menu.styleSheet() == menu_stylesheet("Arial", 14)
        assert [(action.text(), action.data()) for action in leaf_actions(menu)] == [
            ("Alpha", str(alpha)),
            ("● Zulu", str(zulu)),
        ]

    def test_settingsless_submenus_use_default_generated_stylesheet(
        self, tmp_path, monkeypatch
    ):
        """Every recursive menu keeps generated CSS for a settingsless owner."""
        _qt_app()

        from PyQt6.QtWidgets import QMenu

        from kgb_srs.catalog import DatabaseType
        from kgb_srs.main_window import BarskyApp
        from kgb_srs.ui_theme import menu_stylesheet

        db_dir = tmp_path / "db" / "Language-based" / "Sentence-based"
        db_dir.mkdir(parents=True)
        french = db_dir / "French_barsky.db"
        german = db_dir / "German_barsky.db"
        french.write_text("")
        german.write_text("")

        monkeypatch.setattr(
            "kgb_srs.main_window.find_databases",
            lambda *args, **kwargs: [("German", str(german)), ("French", str(french))],
        )
        monkeypatch.setattr(
            "kgb_srs.main_window._open_and_infer_type",
            lambda path: DatabaseType.LANGUAGE_SENTENCE,
        )

        class FakeApp:
            current_db_path = str(french)

        menu = QMenu()
        BarskyApp.build_db_menu(FakeApp(), menu)

        def collect_menus(current_menu):
            menus = [current_menu]
            for action in current_menu.actions():
                if action.menu():
                    menus.extend(collect_menus(action.menu()))
            return menus

        def leaf_actions(current_menu):
            leaves = []
            for action in current_menu.actions():
                if action.menu():
                    leaves.extend(leaf_actions(action.menu()))
                elif action.data():
                    leaves.append(action)
            return leaves

        menus = collect_menus(menu)
        assert len(menus) >= 3
        assert all(item.styleSheet() == menu_stylesheet("Arial", 14) for item in menus)
        assert [(action.text(), action.data()) for action in leaf_actions(menu)] == [
            ("● French", str(french)),
            ("German", str(german)),
        ]

    def test_leaf_action_has_no_submenu(self, tmp_path, monkeypatch):
        """Leaf (database) actions are plain actions, not submenus."""
        _qt_app()

        from kgb_srs.main_window import BarskyApp
        from kgb_srs.catalog import DatabaseType
        from PyQt6.QtWidgets import QMenu

        db_dir = tmp_path / "db" / "Knowledge-based"
        db_dir.mkdir(parents=True)
        dummy = db_dir / "Math_barsky.db"
        dummy.write_text("")

        monkeypatch.setattr(
            "kgb_srs.main_window.find_databases", lambda *a, **k: [("Math", str(dummy))]
        )
        monkeypatch.setattr(
            "kgb_srs.main_window._open_and_infer_type", lambda p: DatabaseType.KNOWLEDGE
        )

        class FakeApp:
            current_db_path = None

        app = FakeApp()
        menu = QMenu()
        BarskyApp.build_db_menu(app, menu)

        # Recursively collect leaf actions (those with data and no submenu)
        def collect_leaves(m):
            result = []
            for a in m.actions():
                if a.menu():
                    result.extend(collect_leaves(a.menu()))
                elif a.data():
                    result.append(a)
            return result

        leaves = collect_leaves(menu)
        assert len(leaves) >= 1
        assert leaves[0].data() == str(dummy)


class TestReviewCanvasGestureLayout:
    """Gesture lanes are in-memory geometry, never scene affordances."""

    @staticmethod
    def _build_review_window(window_size, *, flipped):
        """Return the real canvas and controls after an explicit redraw."""
        from PyQt6.QtWidgets import QApplication

        from kgb_srs.main_window import BarskyApp

        win = BarskyApp()
        win.current_card = (1, "front", "back", 1)
        win.is_current_flipped = flipped
        win.resize(*window_size)
        win.show()
        QApplication.processEvents()
        win.redraw_canvas()
        QApplication.processEvents()
        return win, win.view, win.view.viewport(), win.start_btn

    @staticmethod
    def _assert_no_persistent_grade_target(window):
        """No scene item or legacy field may stand in for a grade action."""
        assert not hasattr(window, "incorrect_zone")
        assert not hasattr(window, "correct_zone")
        assert not hasattr(window, "_zone_y")
        assert all(
            item.__class__.__name__ != "DropZoneItem" for item in window.scene.items()
        )

    def test_unrevealed_canvas_has_full_boundary_without_grade_target(self):
        """Before reveal, explicit Listen/Reveal are the only card actions."""
        _qt_app()
        win, _view, _viewport, _start_btn = self._build_review_window(
            (900, 700), flipped=False
        )
        try:
            scene_rect = win.scene.sceneRect()
            self._assert_no_persistent_grade_target(win)
            assert win._grade_gesture_regions == {}
            assert win._review_card_bottom == pytest.approx(scene_rect.bottom())
            assert win.card_ui is not None
            assert not win.card_ui.tts_btn.isHidden()
            assert not win.card_ui.flip_btn.isHidden()
            assert win.card_ui.incorrect_btn.isHidden()
            assert win.card_ui.correct_btn.isHidden()
        finally:
            win.close()

    @pytest.mark.parametrize("window_size", [(600, 400), (1200, 800)])
    def test_revealed_canvas_uses_contained_nonvisual_lanes(self, window_size):
        """Narrow and wide canvases retain geometry without scene grade lanes."""
        _qt_app()
        from PyQt6.QtCore import QPoint

        win, view, viewport, start_btn = self._build_review_window(
            window_size, flipped=True
        )
        try:
            scene_rect = win.scene.sceneRect()
            regions = win._grade_gesture_regions
            self._assert_no_persistent_grade_target(win)
            assert set(regions) == {"incorrect", "correct"}
            for region in regions.values():
                assert not region.isEmpty()
                assert scene_rect.contains(region)

            lane_top = min(region.top() for region in regions.values())
            assert win._review_card_bottom == pytest.approx(lane_top - 20)
            assert win._review_card_bottom < lane_top
            assert win.card_ui.sceneBoundingRect().bottom() <= (
                win._review_card_bottom + 1
            )

            view_bottom_global = view.mapToGlobal(QPoint(0, view.height())).y()
            button_top_global = start_btn.mapToGlobal(QPoint(0, 0)).y()
            assert button_top_global - view_bottom_global >= 8

            old_regions = regions
            win.redraw_canvas()
            assert win._grade_gesture_regions is not old_regions
            assert set(win._grade_gesture_regions) == {"incorrect", "correct"}
            self._assert_no_persistent_grade_target(win)
        finally:
            win.close()
