"""Preserve the solver's seven station clocks and rail coordinates in the viewer."""

import numpy as np

from rail_vehicle.bogie_model_spec import DIMENSIONS
from rail_vehicle.nonlinear_vehicle import MODEL, VEHICLE_SAMPLE_HZ, case_config, read_case
from rail_vehicle.virtual_trials import json_text
from rail_vehicle.wheel_track import SpatialTrack


def _payload(result):
    if result.get("model")!=MODEL:
        raise ValueError("高级三维回放需要轮轨模型的计算响应。")
    case=read_case(json_text({key:result[key] for key in ("schema","model","source","case")}))
    q,a,station,mount=np.asarray(result["q"]),np.asarray(result["ddq"]),np.asarray(result["station_m"]),np.asarray(result["mount_z_m"])
    track=SpatialTrack(case.route)
    half=case.contact.half_gauge_m
    line=track.scene(np.arange(np.floor(station.min())-8,np.ceil(station.max())+12,.20),half)
    heading=track.bearing(station)
    _,_,bank,rail_shift,rail_z=track.map(station,half)
    starts=np.array([0,5,10,15,19,23,27])
    x0=np.interp(station,line["station_m"],np.asarray(line["center_xy"])[:,0])
    y0=np.interp(station,line["station_m"],np.asarray(line["center_xy"])[:,1])
    heights=np.array([2.45,.95,.95,*([case.contact.radius_m]*4)])
    z=q[:,starts+1]+heights
    common=q[:,starts]*np.cos(bank)-z*np.sin(bank)
    x=x0-common*np.sin(heading)
    y=y0+common*np.cos(heading)
    zp=q[:,starts]*np.sin(bank)+z*np.cos(bank)
    yaw=q[:,[4,9,14,18,22,26,30]]+heading
    pitch=np.column_stack((q[:,[3,8,13]],np.zeros((len(q),4))))
    roll=q[:,starts+2]+bank
    pose=np.stack((x,y,zp,roll,pitch,yaw),axis=-1)
    outputs=result["contact"]
    wheels_heading,theta=heading[:,3:],bank[:,3:]
    signed=np.array([1.,-1.])
    contact_y=(rail_shift[:,3:,None]+half*signed)*np.cos(theta[...,None])-rail_z[:,3:]*np.sin(theta[...,None])
    contact_z=(rail_shift[:,3:,None]+half*signed)*np.sin(theta[...,None])+rail_z[:,3:]*np.cos(theta[...,None])
    contact_positions=np.stack((x0[:,3:,None]-contact_y*np.sin(wheels_heading[...,None]),
                               y0[:,3:,None]+contact_y*np.cos(wheels_heading[...,None]),contact_z),axis=-1)
    travel=mount-mount[0]
    channels={"travel_m":travel[:,0].tolist(),"support_side_l_m":travel[:,0].tolist(),
              "support_side_r_m":travel[:,1].tolist(),"roll_rad":q[:,2].tolist(),"accel_m_s2":a[:,0].tolist(),
              "body_z_accel_m_s2":a[:,1].tolist(),"body_y_m":q[:,0].tolist(),"body_z_m":q[:,1].tolist(),
              "contact_q_min_kn":(np.min(outputs["normal"],axis=(1,2))/1000).tolist(),
              "wheel_rad":(np.asarray(result["time_s"])*case.speed_m_s/case.contact.radius_m).tolist()}
    return case,{"vehicle_model":MODEL,"sample_hz":VEHICLE_SAMPLE_HZ,
                 "parameters":{"id":result["evidence_id"]},"channels":channels,
                 "advanced":{"track":line,"pose":pose.tolist(),"station":station.tolist(),"contact":contact_positions.tolist(),
                             "tread_normal_n":outputs["normal"],"tread_longitudinal_n":outputs["fx"],
                             "tread_lateral_n":outputs["fy"],"contact_heading":wheels_heading.tolist(),
                             "contact_bank":theta.tolist(),"mount_travel":travel.tolist(),
                             "geometry":{"half_gauge":half,"radius":case.contact.radius_m,
                                         "half_wheelbase":DIMENSIONS["wheelbase"]/2,
                                         "bogie_half_spacing":case.vehicle.bogie_half_spacing_m}}}


def playback(result,alternative=None,*,initial_focus="vehicle"):
    case,primary=_payload(result)
    if initial_focus not in ("vehicle","front_frame","rear_frame","front_wheel","front_primary","front_secondary"):
        raise ValueError("轮轨观察位置无效。")
    primary["advanced"]["initial_focus"]=initial_focus
    if alternative is not None:
        other,alternate=_payload(alternative)
        if (case.route,case.speed_m_s,case.duration_s,case.vehicle,case.contact)!=(
                other.route,other.speed_m_s,other.duration_s,other.vehicle,other.contact):
            raise ValueError("双视窗要求同线路、同速度、同车体几何及同时间网格。")
        primary["parameters"]["alternative"]=alternative["evidence_id"]
        alternate["advanced"].pop("track")
        primary["alternative"]=alternate
    return primary
