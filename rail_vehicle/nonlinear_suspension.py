"""Energy-consistent asymmetric mount laws and nominal preload handling."""

from dataclasses import dataclass
import numpy as np

from rail_vehicle.wheel_rail import in_range


@dataclass(frozen=True)
class Mount:
    stiffness_n_m: float
    damping_ns_m: float
    progression_start_m: float = .045
    progression_n_m: float = 300_000.
    stop_m: float = .085
    stop_n_m: float = 3_000_000.
    quadratic_damping_ns2_m2: float = 18_000.

    def __post_init__(self):
        limits=((self.stiffness_n_m,100_000,3e6),(self.damping_ns_m,500,120_000),
                (self.progression_start_m,.015,.09),(self.progression_n_m,0,4e6),
                (self.stop_m,.04,.16),(self.stop_n_m,0,2e7),(self.quadratic_damping_ns2_m2,0,3e5))
        if not all(in_range(*limit) for limit in limits) or self.stop_m <= self.progression_start_m:
            raise ValueError("悬挂刚度、阻尼或限位设置无效。")


def mount_bank(mounts):
    return np.asarray([[m.stiffness_n_m,m.damping_ns_m,m.progression_start_m,m.progression_n_m,
                        m.stop_m,m.stop_n_m,m.quadratic_damping_ns2_m2] for m in mounts]).T


def elastic_force(bank, movement, free_offset):
    k,_,start,extra,stop,buffer,_=bank
    magnitude=np.abs(movement)
    return (k*(movement+free_offset) + np.sign(movement)*
            (extra*np.maximum(magnitude-start,0)+buffer*np.maximum(magnitude-stop,0)))


def dissipative_force(bank, speed):
    return (bank[1]+bank[6]*np.abs(speed))*speed


def potential(bank, movement, free_offset):
    k,_,start,extra,stop,buffer,_=bank
    magnitude=np.abs(movement)
    return .5*(k*(movement+free_offset)**2 + extra*np.maximum(magnitude-start,0)**2
               +buffer*np.maximum(magnitude-stop,0)**2)
