"""Experimental vehicle with unilateral contact, left/right mounts and spatial track input."""

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json

import numpy as np

from rail_vehicle.bogie_model_spec import DIMENSIONS
from rail_vehicle.nonlinear_suspension import Mount, dissipative_force, elastic_force, mount_bank
from rail_vehicle.vehicle_dynamics import VehicleParameters, axle_distances
from rail_vehicle.wheel_rail import Contact, G, in_range, wheel_forces, wheel_generalized
from rail_vehicle.wheel_track import Section, SpatialTrack, Track, body_acceleration

MODEL="rail.vehicle-wheel-rail.v1"
SCHEMA="rail.wheel-rail.case.v1"
BODIES=("body","front_bogie","rear_bogie")
WHEELS=("wheel_1","wheel_2","wheel_3","wheel_4")
DOFS=tuple(f"{name}_{axis}" for name in BODIES for axis in ("y","z","roll","pitch","yaw"))+tuple(
    f"{name}_{axis}" for name in WHEELS for axis in ("y","z","roll","yaw"))
VEHICLE_SAMPLE_HZ=100


@dataclass(frozen=True)
class WheelRailCase:
    vehicle: VehicleParameters = field(default_factory=VehicleParameters)
    contact: Contact = field(default_factory=Contact)
    route: Track = field(default_factory=Track)
    primary_left: Mount = Mount(600_000.,12_500.)
    primary_right: Mount = Mount(600_000.,12_500.)
    secondary_left: Mount = Mount(325_000.,22_500.)
    secondary_right: Mount = Mount(325_000.,22_500.)
    speed_m_s: float=20.
    duration_s: float=3.
    disturbance_m: float=.00045
    antiroll_n_m_rad: float=130_000.
    antiroll_n_m_s_rad: float=8_000.

    def __post_init__(self):
        if (not isinstance(self.vehicle,VehicleParameters) or not isinstance(self.contact,Contact)
                or not isinstance(self.route,Track) or not all(isinstance(m,Mount) for m in (
                    self.primary_left,self.primary_right,self.secondary_left,self.secondary_right))):
            raise ValueError("车辆、接触、线路及悬挂组件必须使用有效配置。")
        if not all(in_range(*b) for b in ((self.speed_m_s,5,55),(self.duration_s,1,8),(self.disturbance_m,0,.003),
                                          (self.antiroll_n_m_rad,0,1e6),(self.antiroll_n_m_s_rad,0,7e4))):
            raise ValueError("初始扰动、速度、时长或抗侧滚参数无效。")
        if abs(self.duration_s*VEHICLE_SAMPLE_HZ-round(self.duration_s*VEHICLE_SAMPLE_HZ))>1e-8:
            raise ValueError("试验时长须位于 0.01 s 输出网格。")


def case_config(case):
    return {"schema":SCHEMA,"model":MODEL,"source":"experimental_teaching","case":asdict(case)}


def read_case(content):
    if not isinstance(content,(str,bytes)) or len(content)>64_000:
        raise ValueError("轮轨工况 JSON 须在 64 KiB 以内。")
    try:
        value=json.loads(content)
        if (set(value)!={"schema","model","source","case"} or value["model"]!=MODEL
                or value["schema"]!=SCHEMA or value["source"]!="experimental_teaching"):
            raise ValueError
        p=value["case"].copy()
        p["vehicle"]=VehicleParameters(**p["vehicle"])
        p["contact"]=Contact(**p["contact"])
        route=p["route"].copy()
        route["sections"]=tuple(Section(**s) for s in route["sections"])
        route["profile"]=tuple(tuple(row) for row in route["profile"])
        p["route"]=Track(**route)
        for name in ("primary_left","primary_right","secondary_left","secondary_right"):
            p[name]=Mount(**p[name])
        return WheelRailCase(**p)
    except (ValueError,KeyError,TypeError,AttributeError,OverflowError,RecursionError):
        raise ValueError("无法读取该版本的轮轨工况。") from None


class VehicleSystem:
    """Small-angle mount geometry, evaluated against separate local track frames."""

    def __init__(self,case):
        self.case=case
        self.track=SpatialTrack(case.route)
        v=case.vehicle
        a,b=v.bogie_half_spacing_m,DIMENSIONS["wheelbase"]/2
        self.distances=np.r_[a+b,b,2*a+b,axle_distances(v)]
        self.indices=np.array([0,5,10,15,19,23,27])
        self.mass=np.array([v.body_mass_kg,v.body_mass_kg,65_000.,v.body_inertia_kg_m2,1_500_000.,
                            *([v.bogie_mass_kg,v.bogie_mass_kg,2200.,v.bogie_inertia_kg_m2,3600.]*2),
                            *([v.wheelset_mass_kg,v.wheelset_mass_kg,740.,780.]*4)])
        self.wheel_indices=self.indices[3:,None]+np.arange(4)
        self.yaw_index=np.r_[self.indices[:3]+4,self.indices[3:]+3]
        qs=[]
        lateral,yaw,roll,mounts,bank_rows=[],[],[],[],[]
        preloads=[]
        upper,lower,offsets=[],[],[]
        linear_k,linear_c,yaw_k,yaw_c=[],[],[],[]
        for u,l,x in ((0,1,a),(0,2,-a),(1,3,b),(1,4,-b),(2,5,b),(2,6,-b)):
            i,j=self.indices[[u,l]]
            secondary=u==0
            base_force=(v.body_mass_kg/4 if secondary else (v.body_mass_kg/2+v.bogie_mass_kg)/4)*G
            reference_k=(650_000. if secondary else 1_200_000.)/2
            span=(DIMENSIONS["side_beam_centres"] if secondary else DIMENSIONS["axlebox_centres"])/2
            for side in (1,-1):
                row=np.zeros(len(DOFS))
                row[[i+1,i+2,i+3,j+1,j+2]]=[1,side*span,x,-1,-side*span]
                qs.append(row)
                preloads.append(-base_force/reference_k)
                mounts.append((case.secondary_left if side>0 else case.secondary_right) if secondary else (
                    case.primary_left if side>0 else case.primary_right))
                bank_rows.append(side*span)
            d=np.zeros(len(DOFS))
            d[[i,i+2,i+4,j,j+2]]=[1,.80 if secondary else .34,x,-1,.40 if secondary else 0.]
            lateral.append(d)
            d=np.zeros(len(DOFS))
            d[[i+4,self.yaw_index[l]]]=[1,-1]
            yaw.append(d)
            if secondary:
                d=np.zeros(len(DOFS))
                d[[i+2,j+2]]=[1,-1]
                roll.append(d)
            upper.append(u)
            lower.append(l)
            offsets.append(x)
            linear_k.append(180_000. if secondary else 580_000.)
            linear_c.append(16_000. if secondary else 32_000.)
            yaw_k.append(170_000. if secondary else 1_500_000.)
            yaw_c.append(12_000. if secondary else 39_000.)
        self.vertical=np.asarray(qs)
        self.lateral,self.yaw,self.roll=map(np.asarray,(lateral,yaw,roll))
        self.bank=np.asarray(bank_rows)
        self.upper,self.lower,self.offsets=map(np.asarray,(upper,lower,offsets))
        self.k_y,self.c_y,self.k_yaw,self.c_yaw=map(np.asarray,(linear_k,linear_c,yaw_k,yaw_c))
        self.mounts=mount_bank(mounts)
        self.free_offset=np.asarray(preloads)
        nominal_n=G*(v.body_mass_kg/4+v.bogie_mass_kg/2+v.wheelset_mass_kg)/2
        self.nominal_n=nominal_n
        self.indentation=(nominal_n/case.contact.normal_n_m32)**(2/3)

    def evaluate(self,t,q,dq,*,speed=None,metrics=False):
        u=self.case.speed_m_s if speed is None else speed
        p=self.case
        where=u*t-self.distances
        half=p.contact.half_gauge_m
        kappa,dk,bank,rail_y,rail_z=self.track.map(where,half)
        bearing=self.track.bearing(where)
        bank_speed=u*self.track.bank_rate(where,half)
        i,j=self.upper,self.lower
        dbank=bank[i]-bank[j]
        dbank_speed=bank_speed[i]-bank_speed[j]
        zmove=self.vertical@q+np.repeat(dbank,2)*self.bank
        zdot=self.vertical@dq+np.repeat(dbank_speed,2)*self.bank
        zforces=elastic_force(self.mounts,zmove,self.free_offset)+dissipative_force(self.mounts,zdot)
        lateral_ref=-self.offsets**2/6*(2*kappa[i]+kappa[j])
        lateral_rate=-u*self.offsets**2/6*(2*dk[i]+dk[j])
        lateral=self.lateral@q+lateral_ref
        yrates=self.lateral@dq+lateral_rate
        yaw=self.yaw@q+bearing[i]-bearing[j]
        yaw_rate=self.yaw@dq+u*(kappa[i]-kappa[j])
        roll=self.roll@q+dbank[:2]
        roll_rate=self.roll@dq+dbank_speed[:2]
        force=(-self.vertical.T@zforces-self.lateral.T@(self.k_y*lateral+self.c_y*yrates)
               -self.yaw.T@(self.k_yaw*yaw+self.c_yaw*yaw_rate)
               -self.roll.T@(p.antiroll_n_m_rad*roll+p.antiroll_n_m_s_rad*roll_rate))
        wheel=wheel_forces(p.contact,q[self.wheel_indices],dq[self.wheel_indices],u,kappa[3:],rail_y[3:],rail_z[3:],self.indentation)
        force[self.wheel_indices]+=wheel_generalized(p.contact,wheel)
        acceleration=force/self.mass
        lat,vert=body_acceleration(u,kappa,bank)
        acceleration[self.indices]+=lat
        acceleration[self.indices+1]+=vert
        acceleration[self.yaw_index]-=u*u*dk
        if metrics:
            return acceleration,{"mount_z_m":zmove,"mount_force_n":-zforces,"wheel":wheel,"station_m":where,
                                 "uncompensated_y_m_s2":lat[0],"secondary_lateral_m":lateral[:2],
                                 "antiroll_torque_nm":-p.antiroll_n_m_rad*roll-p.antiroll_n_m_s_rad*roll_rate}
        return acceleration

    def equilibrium(self):
        from scipy.optimize import root

        initial=np.zeros(len(DOFS))
        # Static roll also changes the transverse mounting coordinates. At zero
        # conicity a common lateral translation is free, so hold the body datum.
        static=np.arange(0 if self.case.contact.conicity else 1,len(DOFS))
        def residual(group):
            q=initial.copy()
            q[static]=group
            return self.evaluate(-2.,q,initial)[static]

        solution=root(residual,initial[static],tol=1e-10)
        q=initial.copy()
        q[static]=solution.x
        test=float(np.max(abs(self.evaluate(-2.,q,initial))))
        if not np.isfinite(test) or test>2e-6:
            raise ValueError("静力平衡求解失败。请检查左右悬挂范围与预载。")
        _,load=self.evaluate(-2.,q,initial,metrics=True)
        if np.any(load["wheel"]["normal"]<=0):
            raise ValueError("静载下已有车轮失去接触。")
        return q,test,load["wheel"]["normal"]


def run_vehicle(case, *, accuracy="standard", checkpoint=lambda:None):
    from scipy.integrate import solve_ivp

    settings={"standard":("DOP853",.012,8e-6,2e-8),"refined":("DOP853",.006,3e-7,4e-10),
              "independent":("RK45",.003,1e-6,1e-9)}
    if accuracy not in settings:
        raise ValueError("请选择常规或精细积分精度。")
    method,step,rtol,atol=settings[accuracy]
    system=VehicleSystem(case)
    q0,residual,static_load=system.equilibrium()
    initial=q0.copy()
    initial[system.wheel_indices[0,0]]+=case.disturbance_m
    n=len(DOFS)
    ticks=np.arange(round(case.duration_s*VEHICLE_SAMPLE_HZ)+1)/VEHICLE_SAMPLE_HZ
    calls=0

    def rhs(t,state):
        nonlocal calls
        calls+=1
        if calls%64==0:
            checkpoint()
        return np.r_[state[n:],system.evaluate(t,state[:n],state[n:])]

    def validity(t,state):
        return min(.065-np.max(abs(state[system.indices])),
                   .22-np.max(abs(state[system.indices+1])),
                   .15-np.max(abs(state[system.yaw_index])))

    validity.terminal=True
    validity.direction=-1
    checkpoint()
    solution=solve_ivp(rhs,(0,case.duration_s),np.r_[initial,np.zeros(n)],method=method,
                       t_eval=ticks,rtol=rtol,atol=atol,max_step=step,events=validity)
    checkpoint()
    if not solution.success or len(solution.t)!=len(ticks) or not np.isfinite(solution.y).all():
        raise ValueError("响应超出实验模型小位移范围，或非线性积分未收敛。")
    q,dq=solution.y[:n].T,solution.y[n:].T
    evaluated=[system.evaluate(t,x,v,metrics=True) for t,x,v in zip(ticks,q,dq,strict=True)]
    stat=[m for _,m in evaluated]
    wheels=[m["wheel"] for m in stat]
    config={**case_config(case),"solver":{"method":method,"accuracy":accuracy,"max_step_s":step,
                                         "rtol":rtol,"atol":atol,"sample_hz":VEHICLE_SAMPLE_HZ}}
    checksum=sha256(json.dumps(config,sort_keys=True,allow_nan=False,separators=(",",":")).encode()).hexdigest()
    return {**config,"configuration_sha256":checksum,"evidence_id":"wheelrail."+checksum[:20],
            "time_s":ticks.tolist(),"q":q.tolist(),"dq":dq.tolist(),"dofs":list(DOFS),
            "ddq":[a.tolist() for a,_ in evaluated],"rhs_count":calls,
            "static_q":q0.tolist(),"static_residual":residual,"static_wheel_n":static_load.tolist(),
            "contact":{k:[w[k].tolist() for w in wheels] for k in ("normal","fx","fy","xi_x","xi_y","flange","demand_ratio","rolling_radius","indentation")},
            **{k:[np.asarray(m[k]).tolist() for m in stat] for k in (
                "station_m","mount_z_m","mount_force_n","secondary_lateral_m","uncompensated_y_m_s2","antiroll_torque_nm")}}


def performance(result):
    a,q,mount=np.asarray(result["ddq"]),np.asarray(result["q"]),np.asarray(result["mount_z_m"])
    loads,lateral,flange,demand=(np.asarray(result["contact"][k]) for k in ("normal","fy","flange","demand_ratio"))
    initial=np.asarray(result["static_wheel_n"])
    travel=mount-np.asarray(result["mount_z_m"][0])
    meaningful=loads > 1e-4*np.min(initial)
    ratio=np.divide(abs(lateral),loads,where=meaningful,out=np.zeros_like(loads))
    index=int(np.argmax(np.max(abs(travel),axis=1)))
    return {"body_y_rms_m_s2":float(np.sqrt(np.mean(a[:,0]**2))),"body_z_rms_m_s2":float(np.sqrt(np.mean(a[:,1]**2))),
            "roll_peak_mrad":float(1000*np.max(abs(q[:,2]-q[0,2]))),
            "wheel_normal_min_kn":float(np.min(loads)/1000),"wheel_normal_max_kn":float(np.max(loads)/1000),
            "wheel_unloading":float(max(0.,np.max(1-loads/initial))),
            "tread_y_over_q":float(np.max(ratio)) if meaningful.any() else None,
            "tread_lateral_peak_kn":float(np.max(abs(lateral))/1000),
            "flange_proxy_peak_kn":float(np.max(abs(flange))/1000),
            "separated_samples":int(np.count_nonzero(np.any(loads<=0,axis=(1,2)))),
            "saturation_demand_samples":int(np.count_nonzero(np.any(demand>1,axis=(1,2)))),
            "linear_limit_samples":int(np.count_nonzero(np.any(demand>.2,axis=(1,2))))
            if result["case"]["contact"]["law"]=="linear" else 0,
            "secondary_travel_peak_mm":float(1000*np.max(abs(travel[:,:4]))),
            "primary_travel_peak_mm":float(1000*np.max(abs(travel[:,4:]))),
            "peak_travel_time_s":result["time_s"][index],
            "uncompensated_body_peak_m_s2":float(np.max(np.abs(result["uncompensated_y_m_s2"])))}


def state_matrix(system,q,speed):
    n=len(q)
    def central(function,step):
        return np.column_stack([(function(v*step)-function(-v*step))/(2*step) for v in np.eye(n)])
    stiffness=central(lambda d:system.evaluate(-2.,q+d,np.zeros(n),speed=speed),2e-7)
    damping=central(lambda v:system.evaluate(-2.,q,v,speed=speed),2e-6)
    return np.block([[np.zeros((n,n)),np.eye(n)],[stiffness,damping]])


def tangent_stability(case,speeds):
    if not 1<=len(speeds)<=25 or not all(in_range(v,5,55) for v in speeds):
        raise ValueError("稳定性扫描需要 1 至 25 个有效速度。")
    system=VehicleSystem(case)
    equilibrium,_,_=system.equilibrium()
    rows=[]
    for velocity in sorted(set(speeds)):
        poles=np.linalg.eigvals(state_matrix(system,equilibrium,velocity))
        leading=poles[int(np.argmax(poles.real))]
        rows.append({"speed_m_s":velocity,"growth_per_s":float(leading.real),
                     "leading_hz":float(abs(leading.imag)/(2*np.pi)),
                     "oscillatory_modes_hz":sorted(float(p.imag/(2*np.pi)) for p in poles if p.imag>1e-6)})
    brackets=[[a["speed_m_s"],b["speed_m_s"]] for a,b in zip(rows,rows[1:])
              if a["growth_per_s"]<0<=b["growth_per_s"]]
    return {"rows":rows,"onset_brackets_m_s":brackets,"scope":"centred-straight small-disturbance tangent"}


def frequency_response(case, frequencies):
    if not 1<=len(frequencies)<=150 or not all(in_range(f,.05,30) for f in frequencies):
        raise ValueError("频响须为 0.05 至 30 Hz 的有效频点。")
    system=VehicleSystem(case)
    q,_,loads=system.equilibrium()
    a=state_matrix(system,q,case.speed_m_s)
    n=len(q)
    derivatives=1.5*case.contact.normal_n_m32*(loads/case.contact.normal_n_m32)**(1/3)
    output=[]
    for f in frequencies:
        w=2*np.pi*f
        phases=np.exp(-1j*w*system.distances[3:]/case.speed_m_s)
        forces=np.zeros(n,dtype=complex)
        forces[system.wheel_indices[:,1]]=derivatives.sum(axis=1)*phases
        forces[system.wheel_indices[:,2]]=case.contact.half_gauge_m*np.diff(derivatives[:,::-1],axis=1).ravel()*phases
        z=np.linalg.solve(-w*w*np.eye(n)-1j*w*a[n:,n:]-a[n:,:n],forces/system.mass)[1]
        output.append({"hz":f,"vertical_gain":float(abs(z)),"body_acceleration_per_m":float(w*w*abs(z))})
    return output
