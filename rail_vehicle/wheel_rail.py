"""Experimental two-point wheelset contact, with SI axes X forward, Y left, Z up."""

from dataclasses import dataclass
from math import isfinite

import numpy as np

G = 9.80665


def in_range(value, lower, upper):
    return type(value) in (int, float) and isfinite(value) and lower <= value <= upper


@dataclass(frozen=True)
class Contact:
    half_gauge_m: float = .75
    radius_m: float = .42
    conicity: float = .10
    normal_n_m32: float = 8.0e10
    creep_x_n: float = 5.0e6
    creep_y_n: float = 4.5e6
    friction: float = .30
    flange_clearance_m: float = .008
    flange_k_n_m: float = 4e6
    law: str = "saturated"

    def __post_init__(self):
        limits = ((self.half_gauge_m,.65,.95),(self.radius_m,.33,.58),(self.conicity,0,.35),
                  (self.normal_n_m32,2e9,2e11),(self.creep_x_n,2e5,1e7),(self.creep_y_n,2e5,1e7),
                  (self.friction,.05,.55),(self.flange_clearance_m,.003,.016),(self.flange_k_n_m,1e6,9e6))
        if self.law not in ("linear","saturated") or not all(in_range(*span) for span in limits):
            raise ValueError("轮轨参数超出实验模型范围。")


def normal_force(coefficient, indentation):
    return coefficient * np.maximum(0., indentation)**1.5


def normal_energy(coefficient, indentation):
    return .4 * coefficient * np.maximum(0., indentation)**2.5


def wheel_forces(spec, state, rates, speed, curvature, rail_y, rail_z, indentation):
    """Left/right tread loads, forces on wheel, creepages and flange surrogate.

    Last axis of state/rates is y, z, roll, yaw. Rail elevations are expressed
    relative to the local canted track plane. Spin is prescribed by speed/r0.
    """
    side = np.array([1.,-1.])
    y,z,roll,yaw = np.moveaxis(state,-1,0)
    vy,_,_,wyaw = np.moveaxis(rates,-1,0)
    delta_y = y - rail_y
    def pair(value):
        return np.asarray(value)[...,None]

    b = spec.half_gauge_m
    penetration = indentation + rail_z - pair(z) - pair(roll)*b*side
    normal = normal_force(spec.normal_n_m32,penetration)
    rolling_r = spec.radius_m + pair(delta_y)*spec.conicity*side
    xi_x = 1. - pair(wyaw/speed+curvature)*b*side - rolling_r/spec.radius_m
    xi_y = np.broadcast_to(pair(vy/speed-yaw),normal.shape)
    fx,fy = -spec.creep_x_n*xi_x,-spec.creep_y_n*xi_y
    tangent_demand = np.hypot(fx,fy)
    capacity = spec.friction*normal
    ratio = np.divide(tangent_demand,capacity,out=np.zeros_like(normal),where=capacity>0)
    scale = np.ones_like(normal)
    if spec.law == "saturated":
        np.divide(np.tanh(ratio),ratio,out=scale,where=ratio>1e-12)
    scale *= normal>0
    flange = -spec.flange_k_n_m*np.sign(delta_y)*np.maximum(abs(delta_y)-spec.flange_clearance_m,0.)
    flange = np.where(np.any(normal>0,axis=-1),flange,0.)
    return {"normal":normal,"fx":fx*scale,"fy":fy*scale,"xi_x":xi_x,"xi_y":xi_y,"flange":flange,
            "demand_ratio":ratio,"indentation":penetration,"rolling_radius":rolling_r}


def wheel_generalized(spec, forces):
    b=spec.half_gauge_m
    q,fx,fy=(forces[k] for k in ("normal","fx","fy"))
    return np.stack((fy.sum(axis=-1)+forces["flange"],q.sum(axis=-1),
                     b*(q[...,0]-q[...,1]),-b*(fx[...,0]-fx[...,1])),axis=-1)


def klingel_wavelength(spec):
    if not spec.conicity:
        return None
    return float(2*np.pi*np.sqrt(spec.half_gauge_m*spec.radius_m/spec.conicity))


def wheelset_linear_matrix(spec, speed, mass=1550., yaw_inertia=760.):
    """Centred straight-running small-creep tangent, q=[y,yaw]."""
    if not in_range(speed,3,80) or not in_range(mass,500,3500) or not in_range(yaw_inertia,100,3500):
        raise ValueError("独立轮对实验速度、质量或惯量无效。")
    b=spec.half_gauge_m
    return np.array([[0,0,1,0],[0,0,0,1],
                     [0,2*spec.creep_y_n/mass,-2*spec.creep_y_n/(mass*speed),0],
                     [-2*b*spec.creep_x_n*spec.conicity/(spec.radius_m*yaw_inertia),
                      0,0,-2*b*b*spec.creep_x_n/(speed*yaw_inertia)]])


def isolated_wheelset(spec, speed, seconds=1.5, displacement_m=.001):
    """Calculate a physical free-wheelset run with prescribed speed, no suspension."""
    from scipy.integrate import solve_ivp

    if not in_range(seconds,.25,4) or not in_range(displacement_m,0,.004):
        raise ValueError("轮对算例时长或初始扰动超出范围。")
    mass=np.array([1550.,1550.,760.,760.])
    matrix=wheelset_linear_matrix(spec,speed)
    preload=mass[0]*G/2
    indent=(preload/spec.normal_n_m32)**(2/3)
    times=np.linspace(0,seconds,round(seconds*200)+1)
    def rhs(_t,u):
        q,v=u[:4],u[4:]
        forces=wheel_forces(spec,q,v,speed,0.,0.,np.zeros(2),indent)
        a=wheel_generalized(spec,forces)/mass
        a[1]-=G
        return np.r_[v,a]

    solution=solve_ivp(rhs,(0,seconds),[displacement_m,0,0,0,0,0,0,0],t_eval=times,
                       method="DOP853",rtol=2e-8,atol=2e-10,max_step=.012)
    if not solution.success or not np.isfinite(solution.y).all():
        raise ValueError("独立轮对积分未收敛。")
    forces=wheel_forces(spec,solution.y[:4].T,solution.y[4:].T,speed,0.,0.,np.zeros(2),indent)
    poles=np.linalg.eigvals(matrix)
    return {"time_s":times.tolist(),"state":solution.y[:4].T.tolist(),
            "normal_n":forces["normal"].tolist(),
            "poles":[[float(p.real),float(p.imag)] for p in poles],"kinematic_wavelength_m":klingel_wavelength(spec)}
