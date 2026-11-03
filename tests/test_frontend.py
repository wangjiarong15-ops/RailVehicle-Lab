"""Home data provenance, native navigation and compact vehicle workflows."""

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from rail_vehicle import db
from rail_vehicle.dashboard import load_dashboard
from rail_vehicle.demo_data import demo_csv_content
from rail_vehicle.run_data import import_run_data, validate_run_csv, list_import_batches
from rail_vehicle.vehicle_data import add_vehicle, update_vehicle, list_vehicles
from rail_vehicle.pages import home, vehicles, run_analysis


def home_app():
    import streamlit as st
    from unittest.mock import patch
    from rail_vehicle.pages import home
    with patch.object(home, "load_dashboard", return_value=st.session_state["fixture"]):
        home.render()


def vehicle_app():
    from rail_vehicle.pages.vehicles import render
    render()


def import_app():
    from rail_vehicle.pages.run_analysis import _render_import
    _render_import()


def records_app():
    import streamlit as st
    from rail_vehicle.ui import record_analysis
    record_analysis("单批次统计分析", dict(batch=st.session_state.get("batch", 1)), 3)


def public_home_app():
    from rail_vehicle.pages.home import render
    render()


def unavailable_home_app():
    import sqlite3
    from unittest.mock import patch
    from rail_vehicle.pages import home
    with patch.object(home, "load_dashboard", side_effect=sqlite3.OperationalError("PRIVATE_DATABASE_DETAIL")):
        home.render()


class FrontendTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "frontend.sqlite3"
        db.init_db(self.path)
        self.stack.enter_context(patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "false"}))
        self.values = dict(vehicle_code="DEMO-001", vehicle_type="虚构测试车型", vehicle_length_m=20.,
                           mass_kg=40000., axle_count=4, bogie_count=2, max_speed_kmh=120., notes="仅测试")
        self.vid = add_vehicle(self.values, self.path)

    def import_batch(self, index=0, code="DEMO-001"):
        content = demo_csv_content(index).replace("DEMO-001", code).encode()
        valid = validate_run_csv(content, list_vehicles(self.path))
        self.assertTrue(valid.is_valid)
        return import_run_data("fictional.csv", valid, self.path)

    def test_dashboard_counts_and_latest_pair_are_database_facts_without_writes(self):
        self.import_batch(0)
        self.import_batch(1)
        add_vehicle(dict(self.values, vehicle_code="OTHER-002"), self.path)
        latest = self.import_batch(0, "OTHER-002")
        before = self.path.read_bytes()
        result = load_dashboard(self.path)
        self.assertEqual((result["vehicle_count"], result["batch_count"], result["sample_count"]), (2, 3, 360))
        self.assertEqual(result["preview"]["batch_id"], latest)
        self.assertEqual(result["preview"]["vehicle_code"], "OTHER-002")
        self.assertEqual(len(result["preview"]["samples"]), 120)
        self.assertEqual(len(result["imports"]), 3)
        self.assertEqual(before, self.path.read_bytes())

    def test_empty_database_and_missing_file_have_explicit_empty_state(self):
        empty = load_dashboard(Path(self.path.parent) / "absent.sqlite3")
        self.assertEqual(empty["vehicle_count"], 0)
        self.assertIsNone(empty["preview"])
        self.assertFalse((Path(self.path.parent) / "absent.sqlite3").exists())
        app = self.home(empty)
        self.assertFalse(app.exception)
        self.assertTrue(any("暂无可预览" in item.value for item in app.info))
        self.assertTrue(any("首次使用" in item.value for item in app.subheader))
        self.assertTrue(any("非实时监测" in item.value for item in app.caption))

    def test_fictional_csv_can_be_validated_without_database_side_effects(self):
        before = self.path.read_bytes()
        validation = validate_run_csv(demo_csv_content(0).encode("utf-8"), list_vehicles(self.path))
        self.assertTrue(validation.is_valid)
        self.assertEqual(validation.source_row_count, 120)
        self.assertEqual(before, self.path.read_bytes())

    def test_import_download_and_database_failure_show_safe_guidance(self):
        with patch.object(run_analysis, "list_vehicles", lambda: list_vehicles(self.path)), \
             patch.object(run_analysis, "list_import_batches", return_value=[]), \
             patch.object(run_analysis, "import_run_data", side_effect=sqlite3.OperationalError("PRIVATE_DETAIL")):
            app = AppTest.from_function(import_app, default_timeout=15).run()
            self.assertEqual(len(app.get("download_button")), 1)
            app.file_uploader[0].upload("fictional.csv", demo_csv_content(0).encode(), "text/csv").run()
            next(button for button in app.button if button.label == "导入到 SQLite").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("导入未完成" in error.value for error in app.error))
            self.assertNotIn("PRIVATE_DETAIL", " ".join(error.value for error in app.error))

    def test_single_vehicle_import_shortcut_selects_exact_vehicle_and_batch(self):
        with patch.object(run_analysis, "list_vehicles", lambda: list_vehicles(self.path)), \
             patch.object(run_analysis, "list_import_batches", lambda: list_import_batches(self.path)), \
             patch.object(run_analysis, "import_run_data", lambda name, valid: import_run_data(name, valid, self.path)):
            app = AppTest.from_function(import_app, default_timeout=15).run()
            app.file_uploader[0].upload("fictional.csv", demo_csv_content(0).encode(), "text/csv").run()
            next(button for button in app.button if button.label == "导入到 SQLite").click().run()
            self.assertFalse(app.exception)
            batches = list_import_batches(self.path)
            self.assertEqual(len(batches), 1)
            app.button(key="import_analyze_latest").click().run()
            self.assertEqual(app.session_state["navigation_page"], "运行分析")
            self.assertEqual(app.session_state["dynamics_vehicle_id"], self.vid)
            self.assertEqual(app.session_state["dynamics_batch_id"], batches[0]["id"])
            self.assertEqual(len(list_import_batches(self.path)), 1)

    def test_multi_vehicle_import_requires_explicit_vehicle_selection(self):
        add_vehicle(dict(self.values, vehicle_code="OTHER-002"), self.path)
        header = "timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel\n"
        csv_data = header + "2026-10-08T09:30:00Z,DEMO-001,80,0.1,0.1\n2026-10-08T09:30:00Z,OTHER-002,81,0.1,0.1\n"
        with patch.object(run_analysis, "list_vehicles", lambda: list_vehicles(self.path)), \
             patch.object(run_analysis, "list_import_batches", lambda: list_import_batches(self.path)), \
             patch.object(run_analysis, "import_run_data", lambda name, valid: import_run_data(name, valid, self.path)):
            app = AppTest.from_function(import_app, default_timeout=15).run()
            app.file_uploader[0].upload("fictional.csv", csv_data.encode(), "text/csv").run()
            next(button for button in app.button if button.label == "导入到 SQLite").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(any(button.key == "import_analyze_latest" for button in app.button))
            self.assertTrue(any("包含多辆车" in caption.value for caption in app.caption))

    def test_vehicle_import_shortcut_opens_import_page(self):
        app = self.vehicle_page()
        app.button(key="vehicle_import").click().run()
        self.assertEqual(app.session_state["navigation_page"], "CSV 数据导入")

    def home(self, fixture=None):
        app = AppTest.from_function(home_app, default_timeout=15)
        app.session_state["fixture"] = load_dashboard(self.path) if fixture is None else fixture
        return app.run()

    def test_home_preview_uses_full_latest_batch_and_current_threshold(self):
        self.import_batch()
        app = self.home()
        self.assertFalse(app.exception)
        values = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(values["采样点总数"], "120 点")
        self.assertEqual(values["统计异常点"], "5")
        self.assertEqual(values["统计异常区段"], "2")
        self.assertEqual(list(app.dataframe[0].value["编号"]), ["#1", "#2"])
        self.assertEqual(len(app.get("plotly_chart")), 1)
        app.number_input(key="home_threshold").set_value(10.).run()
        self.assertEqual({m.label: m.value for m in app.metric}["统计异常区段"], "0")

    def test_oversized_preview_is_not_analyzed_as_partial_batch(self):
        self.import_batch()
        with db.get_connection(self.path) as connection:
            connection.executemany("INSERT INTO run_samples(batch_id,vehicle_id,timestamp,speed_kmh,lateral_accel,vertical_accel) VALUES (1,1,?,80,0,0)",
                                   [("2026-10-08T09:30:00Z",)] * 20000)
        data = load_dashboard(self.path)
        self.assertEqual(data["preview"]["samples"], [])
        app = self.home(data)
        self.assertEqual(len(app.get("plotly_chart")), 0)
        self.assertTrue(any("20,000" in item.value for item in app.info))

    def test_home_event_pagination_uses_full_count_and_matching_global_numbers(self):
        self.import_batch()
        data = load_dashboard(self.path)
        for index, sample in enumerate(data["preview"]["samples"]):
            sample["lateral_accel"] = 2.0 if index % 2 == 0 else 0.0
            sample["vertical_accel"] = 0.0
        app = self.home(data)
        self.assertFalse(app.exception)
        self.assertEqual({m.label: m.value for m in app.metric}["统计异常区段"], "60")
        self.assertEqual(list(app.dataframe[0].value["编号"]), [f"#{i}" for i in range(1, 31)])
        app.selectbox(key="home_event_page").set_value(1).run()
        self.assertFalse(app.exception)
        self.assertEqual(list(app.dataframe[0].value["编号"]), [f"#{i}" for i in range(31, 61)])
        self.assertTrue(any("第 31–60 段" in item.value for item in app.caption))
        app.number_input(key="home_threshold").set_value(3.).run()
        self.assertFalse(app.exception)
        self.assertEqual({m.label: m.value for m in app.metric}["统计异常区段"], "0")
        self.assertFalse(any(box.key == "home_event_page" for box in app.selectbox))

    def test_home_timezone_switch_keeps_metrics_and_event_numbers(self):
        self.import_batch()
        app = self.home()
        metrics = {item.label: item.value for item in app.metric}
        original = app.dataframe[0].value
        app.selectbox(key="home_timezone").set_value("Asia/Shanghai").run()
        self.assertFalse(app.exception)
        self.assertEqual({item.label: item.value for item in app.metric}, metrics)
        localized = app.dataframe[0].value
        self.assertEqual(list(original["编号"]), list(localized["编号"]))
        self.assertTrue(all(value.endswith("+08:00") for value in localized["开始时间（Asia/Shanghai）"]))
        self.assertTrue(any("Asia/Shanghai" in item.value for item in app.caption))

    def test_home_missing_timezone_database_falls_back_to_utc(self):
        from zoneinfo import ZoneInfoNotFoundError
        from rail_vehicle.dashboard_chart import display_timestamp
        self.import_batch()
        def available_clock(value, zone="UTC"):
            if zone == "Asia/Shanghai":
                raise ZoneInfoNotFoundError("missing timezone fixture")
            return display_timestamp(value, zone)
        with patch.object(home, "display_timestamp", side_effect=available_clock):
            app = self.home()
            app.selectbox(key="home_timezone").set_value("Asia/Shanghai").run()
        self.assertFalse(app.exception)
        self.assertTrue(any("暂按 UTC" in item.value for item in app.warning))
        self.assertIn("开始时间（UTC）", app.dataframe[0].value.columns)

    def test_bad_preview_reports_safe_error_instead_of_inventing_metrics(self):
        self.import_batch()
        data = load_dashboard(self.path)
        data["preview"]["samples"][0]["speed_kmh"] = float("nan")
        app = self.home(data)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get("plotly_chart")), 0)
        self.assertTrue(any("无法分析" in item.value for item in app.warning))

    def test_public_home_never_reads_database_or_displays_asset_totals(self):
        with patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "true"}), patch.object(home, "load_dashboard", side_effect=AssertionError("private database")):
            app = AppTest.from_function(public_home_app).run()
            self.assertFalse(app.exception)
            self.assertTrue(all(metric.value == "—" for metric in app.metric))
            self.assertTrue(any("内置虚构演示" in item.value for item in app.caption))
            self.assertEqual(len(app.dataframe), 0)

    def test_session_history_only_records_executed_unique_analysis(self):
        app = AppTest.from_function(records_app).run()
        app.run()
        self.assertEqual(len(app.session_state["recent_analysis_records"]), 1)
        app.session_state["batch"] = 2
        app.run()
        self.assertEqual(len(app.session_state["recent_analysis_records"]), 2)
        self.assertEqual(app.session_state["recent_analysis_records"][0]["数据点数"], 3)

    def test_home_recent_cards_show_actual_session_records_without_fingerprint(self):
        app = AppTest.from_function(records_app).run()
        records = deepcopy(app.session_state["recent_analysis_records"])
        home_page = AppTest.from_function(home_app)
        home_page.session_state["fixture"] = load_dashboard(self.path)
        home_page.session_state["recent_analysis_records"] = records
        home_page.run()
        self.assertFalse(home_page.exception)
        text = " ".join(item.value for item in [*home_page.markdown, *home_page.caption])
        self.assertIn("单批次统计分析", text)
        self.assertIn("3 个样本", text)
        self.assertNotIn(records[0]["fingerprint"], text)

    def test_home_database_failure_is_safe_and_does_not_show_zero_as_real_total(self):
        app = AppTest.from_function(unavailable_home_app).run()
        self.assertFalse(app.exception)
        self.assertTrue(all(metric.value == "—" for metric in app.metric))
        self.assertTrue(app.warning)
        self.assertNotIn("PRIVATE_DATABASE_DETAIL", " ".join(item.value for item in app.warning))

    def vehicle_page(self):
        self.stack.enter_context(patch.object(vehicles, "list_vehicles", lambda: list_vehicles(self.path)))
        self.stack.enter_context(patch.object(vehicles, "add_vehicle", lambda values: add_vehicle(values, self.path)))
        self.stack.enter_context(patch.object(vehicles, "update_vehicle", lambda vid, values: update_vehicle(vid, values, self.path)))
        return AppTest.from_function(vehicle_app, default_timeout=15).run()

    def test_vehicle_default_is_searchable_list_with_details_and_no_large_form(self):
        app = self.vehicle_page()
        self.assertEqual(len(app.get("form")), 0)
        self.assertEqual(len(app.dataframe[0].value), 1)
        self.assertEqual(len(app.table), 1)
        app.text_input(key="vehicle_search").set_value("not-found").run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe), 0)
        self.assertTrue(any("暂无匹配" in item.value for item in app.info))

    def test_vehicle_dialog_create_validates_and_persists(self):
        app = self.vehicle_page()
        app.button(key="vehicle_add").click().run()
        app.button(key="FormSubmitter:vehicle_create_form-保存车辆").click().run()
        self.assertTrue(app.error)
        self.assertEqual(len(list_vehicles(self.path)), 1)
        app.text_input(key="vehicle_new_vehicle_code").set_value("NEW-002")
        app.text_input(key="vehicle_new_vehicle_type").set_value("新增虚构车型")
        app.button(key="FormSubmitter:vehicle_create_form-保存车辆").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(list_vehicles(self.path)), 2)
        self.assertEqual(len(app.get("form")), 0)

    def test_vehicle_dialog_edit_saves_values_and_duplicate_is_rejected(self):
        add_vehicle(dict(self.values, vehicle_code="OTHER-002"), self.path)
        app = self.vehicle_page()
        app.button(key="vehicle_edit_open").click().run()
        app.text_input(key="vehicle_edit_vehicle_code").set_value("OTHER-002")
        app.button(key="FormSubmitter:vehicle_edit_form-保存修改").click().run()
        self.assertTrue(app.error)
        app.text_input(key="vehicle_edit_vehicle_code").set_value("UPDATED-001")
        app.number_input(key="vehicle_edit_mass_kg").set_value(45000.)
        app.button(key="FormSubmitter:vehicle_edit_form-保存修改").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any(row["vehicle_code"] == "UPDATED-001" and row["mass_kg"] == 45000 for row in list_vehicles(self.path)))

    def test_vehicle_shortcut_selects_correct_analysis_vehicle(self):
        app = self.vehicle_page()
        app.button(key="vehicle_analyze").click().run()
        self.assertEqual(app.session_state["navigation_page"], "运行分析")
        self.assertEqual(app.session_state["dynamics_vehicle_id"], self.vid)

    def test_root_navigation_groups_and_import_entry_preserve_all_pages(self):
        with patch("rail_vehicle.db.init_db"), patch.object(db, "DB_PATH", self.path), \
             patch.object(run_analysis, "list_vehicles", lambda: list_vehicles(self.path)), \
             patch.object(run_analysis, "list_import_batches", lambda: list_import_batches(self.path)):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["navigation_page"], "平台首页")
            self.assertEqual(len(app.sidebar.radio), 0)
            self.assertEqual(app.button(key="nav_平台首页").proto.type, "primary")
            for label in ("车辆参数", "运行分析", "CSV 数据导入", "动态运行驾驶舱", "机器学习异常检测", "振动与平稳性分析", "多车辆运行对比", "统计与异常演示", "演示中心"):
                self.assertIsNotNone(app.button(key=f"nav_{label}"))
            app.button(key="nav_CSV 数据导入").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.file_uploader), 1)
