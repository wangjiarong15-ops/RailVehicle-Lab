"""Vehicle parameter entry, browsing, and editing page."""

import streamlit as st

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
        st.info("还没有车辆记录。请先使用上方表单新增车辆。")
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
        use_container_width=True,
    )


def render() -> None:
    st.header("车辆参数")
    st.caption("录入并维护车辆基础信息。带 * 的字段为必填项，数值必须大于 0。")

    vehicles = list_vehicles()
    mode = st.radio("操作", ["新增车辆", "编辑车辆"], horizontal=True)

    if mode == "新增车辆":
        with st.form("vehicle_create_form", clear_on_submit=True):
            values = _vehicle_form("vehicle_new")
            submitted = st.form_submit_button("保存车辆", type="primary")
        if submitted:
            try:
                add_vehicle(values)
            except (ValueError, DuplicateVehicleCodeError) as exc:
                st.error(str(exc))
            else:
                st.success(f"车辆“{values['vehicle_code'].strip()}”已保存。")
                vehicles = list_vehicles()
    elif not vehicles:
        st.info("当前没有可编辑的车辆，请先新增车辆。")
    else:
        selected_id = st.selectbox(
            "选择要编辑的车辆",
            options=[vehicle["id"] for vehicle in vehicles],
            format_func=lambda vehicle_id: next(
                f"{vehicle['vehicle_code']} · {vehicle['vehicle_type']}"
                for vehicle in vehicles
                if vehicle["id"] == vehicle_id
            ),
            key="edit_vehicle_id",
            on_change=_clear_edit_fields,
        )
        selected_vehicle = next(
            vehicle for vehicle in vehicles if vehicle["id"] == selected_id
        )
        with st.form("vehicle_edit_form"):
            values = _vehicle_form("vehicle_edit", selected_vehicle)
            submitted = st.form_submit_button("保存修改", type="primary")
        if submitted:
            try:
                update_vehicle(selected_id, values)
            except (ValueError, DuplicateVehicleCodeError, VehicleNotFoundError) as exc:
                st.error(str(exc))
            else:
                st.success(f"车辆“{values['vehicle_code'].strip()}”的修改已保存。")
                vehicles = list_vehicles()

    _render_vehicle_list(vehicles)
