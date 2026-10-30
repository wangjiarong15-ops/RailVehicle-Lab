"""Vehicle parameter entry, browsing, and editing page."""

import streamlit as st
import sqlite3
from rail_vehicle.ui import navigate

from rail_vehicle.vehicle_data import (
    DuplicateVehicleCodeError,
    VehicleNotFoundError,
    add_vehicle,
    list_vehicles,
    update_vehicle,
)


VEHICLE_FIELDS = {
    "vehicle_code": "车辆编号",
    "vehicle_type": "车型",
    "vehicle_length_m": "车辆长度（米）",
    "mass_kg": "车辆质量（千克）",
    "axle_count": "轴数",
    "bogie_count": "转向架数量",
    "max_speed_kmh": "最高运行速度（千米/小时）",
    "notes": "备注",
}


def _clear_edit_fields() -> None:
    for field in VEHICLE_FIELDS:
        st.session_state.pop(f"vehicle_edit_{field}", None)


def _vehicle_form(prefix: str, vehicle: dict | None = None) -> dict:
    """Render the shared add/edit fields and return the entered values."""
    vehicle = vehicle or {}
    col_left, col_right = st.columns(2)
    with col_left:
        vehicle_code = st.text_input(
            "车辆编号 *",
            value=vehicle.get("vehicle_code", ""),
            max_chars=50,
            key=f"{prefix}_vehicle_code",
            help="必填，且不能与已有车辆编号重复。",
        )
        vehicle_type = st.text_input(
            "车型 *",
            value=vehicle.get("vehicle_type", ""),
            max_chars=100,
            key=f"{prefix}_vehicle_type",
        )
        vehicle_length_m = st.number_input(
            "车辆长度（米） *",
            min_value=0.01,
            value=max(float(vehicle.get("vehicle_length_m", 20.0) or 20.0), 0.01),
            step=0.1,
            key=f"{prefix}_vehicle_length_m",
        )
        mass_kg = st.number_input(
            "车辆质量（千克） *",
            min_value=0.01,
            value=max(float(vehicle.get("mass_kg", 40000.0) or 40000.0), 0.01),
            step=100.0,
            key=f"{prefix}_mass_kg",
        )
    with col_right:
        axle_count = st.number_input(
            "轴数 *",
            min_value=1,
            value=max(int(vehicle.get("axle_count", 4) or 4), 1),
            step=1,
            key=f"{prefix}_axle_count",
        )
        bogie_count = st.number_input(
            "转向架数量 *",
            min_value=1,
            value=max(int(vehicle.get("bogie_count", 2) or 2), 1),
            step=1,
            key=f"{prefix}_bogie_count",
        )
        max_speed_kmh = st.number_input(
            "最高运行速度（千米/小时） *",
            min_value=0.01,
            value=max(float(vehicle.get("max_speed_kmh", 120.0) or 120.0), 0.01),
            step=1.0,
            key=f"{prefix}_max_speed_kmh",
        )
        notes = st.text_area(
            "备注",
            value=vehicle.get("notes", "") or "",
            max_chars=2000,
            key=f"{prefix}_notes",
            height=100,
        )
    return {
        "vehicle_code": vehicle_code,
        "vehicle_type": vehicle_type,
        "vehicle_length_m": vehicle_length_m,
        "mass_kg": mass_kg,
        "axle_count": axle_count,
        "bogie_count": bogie_count,
        "max_speed_kmh": max_speed_kmh,
        "notes": notes,
    }


def _render_vehicle_list(vehicles: list[dict]) -> None:
    st.subheader("车辆列表")
    if not vehicles:
        st.info("暂无匹配车辆。可调整搜索条件，或点击“新增车辆”建立档案。")
        return

    labels = {
        "vehicle_code": "车辆编号",
        "vehicle_type": "车型",
        "vehicle_length_m": "长度（米）",
        "mass_kg": "质量（千克）",
        "axle_count": "轴数",
        "bogie_count": "转向架数量",
        "max_speed_kmh": "最高速度（千米/小时）",
        "notes": "备注",
    }
    st.dataframe(
        [{labels[key]: vehicle[key] for key in labels} for vehicle in vehicles],
        hide_index=True,
        width="stretch",
    )


def _open_editor(vehicle=None):
    prefix = "vehicle_edit" if vehicle is not None else "vehicle_new"
    for field in VEHICLE_FIELDS:
        st.session_state.pop(f"{prefix}_{field}", None)
    st.session_state["vehicle_editor"] = {"vehicle": vehicle}


def _close_editor():
    st.session_state.pop("vehicle_editor", None)


@st.dialog("车辆档案", width="large", on_dismiss=_close_editor)
def _vehicle_dialog(vehicle=None):
    editing = vehicle is not None
    st.caption("编辑车辆" if editing else "新增车辆 · 带 * 为必填，数值必须大于 0")
    with st.form("vehicle_edit_form" if editing else "vehicle_create_form"):
        values = _vehicle_form("vehicle_edit" if editing else "vehicle_new", vehicle)
        submitted = st.form_submit_button("保存修改" if editing else "保存车辆", type="primary")
    if submitted:
        try:
            update_vehicle(vehicle["id"], values) if editing else add_vehicle(values)
        except (ValueError, DuplicateVehicleCodeError, VehicleNotFoundError) as exc:
            st.error(str(exc))
        except (sqlite3.Error, OSError):
            st.error("暂时无法保存车辆，请检查数据库后重试。")
        else:
            st.session_state["vehicle_flash"] = "车辆档案已保存。"
            _close_editor()
            st.rerun()


def render() -> None:
    st.header("车辆参数")
    st.caption("数据工作台 / 车辆档案 · 参数维护 · 运行分析")
    if message := st.session_state.pop("vehicle_flash", None):
        st.success(message)
    try:
        vehicles = list_vehicles()
    except (sqlite3.Error, OSError):
        st.error("暂时无法读取车辆档案，请检查数据库后重试。")
        return
    search, action = st.columns([4, 1], vertical_alignment="bottom")
    query = search.text_input("搜索车辆", placeholder="输入车辆编号或车型", key="vehicle_search").strip().casefold()
    action.button("＋ 新增车辆", type="primary", key="vehicle_add", width="stretch", on_click=_open_editor)
    if editor := st.session_state.get("vehicle_editor"):
        _vehicle_dialog(editor["vehicle"])
    matches = [vehicle for vehicle in vehicles if query in vehicle["vehicle_code"].casefold() or query in vehicle["vehicle_type"].casefold()]
    st.caption(f"登记车辆 {len(vehicles)} 辆 · 当前匹配 {len(matches)} 辆")
    with st.container(border=True):
        _render_vehicle_list(matches)
    if not matches:
        return
    by_id = {vehicle["id"]: vehicle for vehicle in matches}
    if st.session_state.get("vehicle_selected_id") not in by_id:
        st.session_state.pop("vehicle_selected_id", None)
    selection, edit, analysis = st.columns([3, 1, 1], vertical_alignment="bottom")
    selected_id = selection.selectbox("当前车辆", list(by_id), format_func=lambda value: f"{by_id[value]['vehicle_code']} · {by_id[value]['vehicle_type']}", key="vehicle_selected_id")
    selected = by_id[selected_id]
    edit.button("编辑车辆", key="vehicle_edit_open", width="stretch", on_click=_open_editor, args=(selected,))
    analysis.button("进入分析 →", key="vehicle_analyze", width="stretch", on_click=navigate,
                    args=("运行分析",), kwargs={"dynamics_vehicle_id": selected_id})
    with st.expander("查看车辆详情", expanded=False):
        st.table([{ "参数": label, "值": str(selected.get(field, "")) } for field, label in VEHICLE_FIELDS.items() if field != "notes"])
        st.text(selected.get("notes", "") or "未填写备注")
