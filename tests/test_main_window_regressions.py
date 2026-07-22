"""Regression tests for main-window composition and layout."""

import os

import pytest

from .qt_helpers import qt_app as _qt_app


class TestNoSyncHTTPInMainWindow:
    """Main window must not contain processEvents or synchronous HTTP calls."""

    def test_no_process_events(self):
        import os
        main_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "kgb_srs", "main_window.py")
        with open(main_path, "r") as f:
            content = f.read()
        assert "processEvents" not in content, \
            "processEvents found in main_window.py"
        assert "_make_http_call" not in content, \
            "_make_http_call found in main_window.py"
        assert "urllib.request.urlopen" not in content, \
            "urllib.request.urlopen found in main_window.py"


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

    def test_db_menu_stylesheet_constant_exists(self):
        """_DB_MENU_STYLESHEET must be defined with padding-right."""
        from kgb_srs.main_window import _DB_MENU_STYLESHEET

        assert isinstance(_DB_MENU_STYLESHEET, str)
        assert "padding-right" in _DB_MENU_STYLESHEET
        assert "QMenu::item" in _DB_MENU_STYLESHEET

    def test_db_menu_stylesheet_has_vertical_padding(self):
        """_DB_MENU_STYLESHEET must include padding-top >=6px and
        padding-bottom >=6px for readable row height."""
        import re
        from kgb_srs.main_window import _DB_MENU_STYLESHEET

        m_top = re.search(r"padding-top\s*:\s*(\d+)px", _DB_MENU_STYLESHEET)
        m_bottom = re.search(r"padding-bottom\s*:\s*(\d+)px", _DB_MENU_STYLESHEET)

        assert m_top is not None, "stylesheetsheet missing padding-top"
        assert m_bottom is not None, "stylesheetsheet missing padding-bottom"

        top_px = int(m_top.group(1))
        bottom_px = int(m_bottom.group(1))

        assert top_px >= 6, f"padding-top is {top_px}px, expected >= 6px"
        assert bottom_px >= 6, f"padding-bottom is {bottom_px}px, expected >= 6px"

    def test_root_menu_has_stylesheet(self, tmp_path, monkeypatch):
        """The root QMenu returned by build_db_menu carries the stylesheet."""
        _qt_app()

        from kgb_srs.main_window import BarskyApp, _DB_MENU_STYLESHEET
        from kgb_srs.catalog import DatabaseType
        from PyQt6.QtWidgets import QMenu

        # Build canonical path: Language-based/Sentence-based/Test
        db_dir = tmp_path / "db" / "Language-based" / "Sentence-based"
        db_dir.mkdir(parents=True)
        dummy = db_dir / "Test_barsky.db"
        dummy.write_text("")

        monkeypatch.setattr("kgb_srs.main_window.find_databases",
                            lambda *a, **k: [("Test", str(dummy))])
        monkeypatch.setattr("kgb_srs.main_window._open_and_infer_type",
                            lambda p: DatabaseType.LANGUAGE_SENTENCE)

        class FakeApp:
            current_db_path = None
        app = FakeApp()
        menu = QMenu()
        BarskyApp.build_db_menu(app, menu)

        stylesheet = menu.styleSheet()
        assert stylesheet == _DB_MENU_STYLESHEET
        assert "padding-right" in stylesheet

    def test_submenus_inherit_stylesheet(self, tmp_path, monkeypatch):
        """Every recursive submenu must carry the same right-padding stylesheet."""
        _qt_app()

        from kgb_srs.main_window import BarskyApp, _DB_MENU_STYLESHEET
        from kgb_srs.catalog import DatabaseType
        from PyQt6.QtWidgets import QMenu

        # Build a deeper hierarchy: Language-based > Sentence-based > French
        db_dir = tmp_path / "db" / "Language-based" / "Sentence-based"
        db_dir.mkdir(parents=True)
        dummy = db_dir / "French_barsky.db"
        dummy.write_text("")

        monkeypatch.setattr("kgb_srs.main_window.find_databases",
                            lambda *a, **k: [("French", str(dummy))])
        monkeypatch.setattr("kgb_srs.main_window._open_and_infer_type",
                            lambda p: DatabaseType.LANGUAGE_SENTENCE)

        class FakeApp:
            current_db_path = None
        app = FakeApp()
        menu = QMenu()
        BarskyApp.build_db_menu(app, menu)

        def collect_menus(m):
            result = [m]
            for action in m.actions():
                if action.menu():
                    result.extend(collect_menus(action.menu()))
            return result

        all_menus = collect_menus(menu)
        # We expect at least 3: root, Language-based, Sentence-based
        assert len(all_menus) >= 3

        for m in all_menus:
            assert m.styleSheet() == _DB_MENU_STYLESHEET, (
                f"Menu '{m.title()}' missing stylesheet"
            )
            assert "padding-right" in m.styleSheet()

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

        monkeypatch.setattr("kgb_srs.main_window.find_databases",
                            lambda *a, **k: [("Math", str(dummy))])
        monkeypatch.setattr("kgb_srs.main_window._open_and_infer_type",
                            lambda p: DatabaseType.KNOWLEDGE)

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


class TestDropZoneLayoutDoesNotOverflow:
    """DropZoneItems must be fully inside the viewport with a visible
    internal inset, and the QGraphicsView must be separated from the
    review buttons by a clear external layout gap."""

    # ── helpers ────────────────────────────────────────────────────────
    @staticmethod
    def _build_and_measure(window_size):
        """Build BarskyApp at *window_size*, process events, and return
        the (window, view, viewport, incorrect_zone, start_btn)."""
        from PyQt6.QtWidgets import QApplication
        from kgb_srs.main_window import BarskyApp

        win = BarskyApp()
        win.resize(*window_size)
        win.show()
        QApplication.processEvents()
        win.redraw_canvas()
        QApplication.processEvents()
        return win, win.view, win.view.viewport(), win.incorrect_zone, win.start_btn

    # ── tests ──────────────────────────────────────────────────────────
    def test_zone_fully_inside_viewport_with_inset(self):
        """At a realistic default size the full zone bounding rect must
        lie inside the viewport, and the zone bottom must leave ≥ 10 px
        internal inset from the viewport bottom edge."""
        _qt_app()

        win, view, vp, zone, start_btn = self._build_and_measure((900, 700))

        assert zone is not None, "incorrect zone not created"
        sr = zone.sceneBoundingRect()
        vr = view.mapFromScene(sr).boundingRect()

        # Zone must be fully inside the viewport — no clipping.
        assert vr.y() >= 0, f"zone top clipped: {vr.y()} px above viewport"
        assert vr.bottom() <= vp.height(), (
            f"zone bottom {vr.bottom()} exceeds viewport height {vp.height()}"
        )

        # Internal inset: zone bottom must be ≥ 10 px above viewport bottom.
        inset = vp.height() - vr.bottom()
        assert inset >= 10, (
            f"zone-bottom → viewport-bottom inset is {inset} px, need ≥ 10"
        )

        win.close()

    def test_view_to_button_external_gap(self):
        """The QGraphicsView outer bottom edge must be ≥ 8 px above the
        Start Daily Review button top in global coordinates."""
        _qt_app()
        from PyQt6.QtCore import QPoint

        win, view, vp, zone, start_btn = self._build_and_measure((900, 700))

        view_bottom_global = view.mapToGlobal(QPoint(0, view.height())).y()
        btn_top_global = start_btn.mapToGlobal(QPoint(0, 0)).y()
        external_gap = btn_top_global - view_bottom_global
        assert external_gap >= 8, (
            f"view outer bottom → button top gap is {external_gap} px, need ≥ 8"
        )

        win.close()

    def test_zone_to_button_total_gap(self):
        """The total gap from zone painted bottom to button top must be
        clearly visible (≥ 18 px in global coordinates)."""
        _qt_app()
        from PyQt6.QtCore import QPoint

        win, view, vp, zone, start_btn = self._build_and_measure((900, 700))

        sr = zone.sceneBoundingRect()
        vr = view.mapFromScene(sr).boundingRect()
        zone_bottom_global = vp.mapToGlobal(
            QPoint(int(vr.center().x()), int(vr.bottom()))
        ).y()
        btn_top_global = start_btn.mapToGlobal(QPoint(0, 0)).y()
        total_gap = btn_top_global - zone_bottom_global
        assert total_gap >= 18, (
            f"zone bottom → button top total gap is {total_gap} px, need ≥ 18"
        )

        win.close()

    def test_holds_at_minimum_viable_height(self):
        """The constraints must hold at the minimum view height of 200
        where the viewport is still large enough for the zone."""
        _qt_app()
        from PyQt6.QtCore import QPoint

        # 600×400 keeps the view at its 200 px minimum height without
        # pushing Qt into an unresolvable size negotiation.
        win, view, vp, zone, start_btn = self._build_and_measure((600, 400))

        assert zone is not None
        sr = zone.sceneBoundingRect()
        vr = view.mapFromScene(sr).boundingRect()

        assert vr.y() >= 0
        inset = vp.height() - vr.bottom()
        assert inset >= 10, (
            f"at min height: zone inset = {inset} px, need ≥ 10"
        )

        view_bottom_global = view.mapToGlobal(QPoint(0, view.height())).y()
        btn_top_global = start_btn.mapToGlobal(QPoint(0, 0)).y()
        external_gap = btn_top_global - view_bottom_global
        assert external_gap >= 8, (
            f"at min height: external gap = {external_gap} px, need ≥ 8"
        )

        win.close()
