"""Continuous curvature/cant profiles and spatial excitation for both rails."""

from dataclasses import dataclass
import numpy as np

from rail_vehicle.wheel_rail import G, in_range


@dataclass(frozen=True)
class Section:
    metres: float
    end_curvature_m_inv: float
    end_cant_m: float

    def __post_init__(self):
        if not (in_range(self.metres,5,5000) and in_range(self.end_curvature_m_inv,-.01,.01)
                and in_range(self.end_cant_m,-.16,.16)):
            raise ValueError("线路区段长度、曲率或超高超出实验范围。")


@dataclass(frozen=True)
class Track:
    sections: tuple = (Section(8.,0.,0.),Section(24.,1/450,.08),Section(200.,1/450,.08))
    wavelength_m: float = 8.
    align_m: float = .001
    left_m: float = .0015
    right_m: float = .001
    profile: tuple = ()

    def __post_init__(self):
        if not (isinstance(self.sections,tuple) and 1<=len(self.sections)<=12
                and all(isinstance(sec,Section) for sec in self.sections)
                and in_range(self.wavelength_m,2,40) and all(in_range(x,0,.012) for x in (self.align_m,self.left_m,self.right_m))):
            raise ValueError("线路至少需要一个有效区段及合法空间激励。")
        if not isinstance(self.profile,tuple) or len(self.profile)>140:
            raise ValueError("自定义线路不平顺最多为 140 个控制点。")
        if self.profile and (len(self.profile)<2 or not all(
                isinstance(row,tuple) and len(row)==4 and in_range(row[0],0,2000)
                and all(in_range(x,-.016,.016) for x in row[1:]) for row in self.profile)
                or self.profile[0]!=(0,0,0,0) or self.profile[-1][1:]!=(0,0,0)
                or any(b[0]-a[0]<.15 for a,b in zip(self.profile,self.profile[1:]))):
            raise ValueError("自定义采样点须按距离递增且首尾为零。")


def template(name="curve",radius_m=450.,cant_m=.08,left=.0015,right=.001,align=.001):
    if name=="straight":
        return Track((Section(300.,0.,0.),),left_m=left,right_m=right,align_m=align)
    if name not in ("curve","reverse") or not in_range(abs(radius_m),125,2000) or not in_range(cant_m,-.16,.16):
        raise ValueError("曲线半径、线路模板或超高不适用。")
    first=Section(8.,0.,0.)
    if name=="reverse":
        sections=(first,Section(22.,1/radius_m,cant_m),Section(17.,1/radius_m,cant_m),
                  Section(35.,-1/radius_m,-cant_m),Section(30.,-1/radius_m,-cant_m),Section(30.,0.,0.))
    else:
        sections=(first,Section(24.,1/radius_m,cant_m),Section(250.,1/radius_m,cant_m))
    return Track(sections,left_m=left,right_m=right,align_m=align)


class SpatialTrack:
    """Exact heading integral for linearly interpolated section curvatures."""
    def __init__(self,track):
        self.track=track
        lengths=np.array([s.metres for s in track.sections])
        self.distance=np.r_[0.,np.cumsum(lengths)]
        self.curvature=np.array([0.,*(s.end_curvature_m_inv for s in track.sections)])
        self.cant=np.array([0.,*(s.end_cant_m for s in track.sections)])
        self.cant_rate=np.diff(self.cant)/lengths
        self.derivative=np.diff(self.curvature)/lengths
        self.heading=np.r_[0.,np.cumsum(lengths*(self.curvature[:-1]+self.curvature[1:])/2)]
        self.profile=np.array(track.profile).T if track.profile else None

    def map(self,station,half_gauge):
        """Return curvature, its spatial derivative, bank, track shift, rail elevations."""
        x=np.asarray(station)
        k=np.interp(x,self.distance,self.curvature)
        derivative=self.derivative[np.clip(np.searchsorted(self.distance,x,side="right")-1,0,len(self.derivative)-1)]
        derivative=np.where((x>=0)&(x<self.distance[-1]),derivative,0.)
        # Positive signed cant raises the right rail (outer rail in a left curve).
        bank=-np.arctan(np.interp(x,self.distance,self.cant)/(2*half_gauge))
        if self.profile is not None:
            y,l,r=(np.interp(x,self.profile[0],data,left=0.,right=0.) for data in self.profile[1:])
        else:
            phase=2*np.pi*x/self.track.wavelength_m
            ramp=(1-np.cos(np.pi*np.clip(x/self.track.wavelength_m,0,1)))/2
            y=self.track.align_m*ramp*np.sin(phase)
            l=self.track.left_m*ramp*np.sin(phase)
            r=self.track.right_m*ramp*np.sin(phase+.45)
        return k,derivative,bank,y,np.stack((l,r),axis=-1)

    def bearing(self,station):
        x=np.asarray(station)
        index=np.clip(np.searchsorted(self.distance,x,side="right")-1,0,len(self.derivative)-1)
        dx=np.minimum(x,self.distance[-1])-self.distance[index]
        angle=self.heading[index]+self.curvature[index]*dx+self.derivative[index]*dx**2/2
        return np.where(x<0,0.,angle+np.maximum(0.,x-self.distance[-1])*self.curvature[-1])

    def bank_rate(self,station,half_gauge):
        x=np.asarray(station)
        index=np.clip(np.searchsorted(self.distance,x,side="right")-1,0,len(self.derivative)-1)
        cant=np.interp(x,self.distance,self.cant)
        rate=-self.cant_rate[index]/(2*half_gauge)/(1+(cant/(2*half_gauge))**2)
        return np.where((x>=0)&(x<self.distance[-1]),rate,0.)

    def scene(self,stations,half_gauge):
        """Integrate the exact local bearing on the same spatial track used by the solver."""
        s=np.asarray(stations)
        h=self.bearing(s)
        ds=np.diff(s)
        mid=self.bearing((s[1:]+s[:-1])/2)
        yaw=self.bearing(s[0])
        xy0=np.array([s[0]*np.cos(yaw),s[0]*np.sin(yaw)])
        xy=xy0 + np.vstack((np.zeros(2),np.cumsum(ds[:,None]*np.column_stack((np.cos(mid),np.sin(mid))),axis=0)))
        k,_,bank,y,z=self.map(s,half_gauge)
        sides=np.array([1.,-1.])
        normal=np.column_stack((-np.sin(h),np.cos(h)))
        local_y=(y[:,None]+half_gauge*sides)*np.cos(bank[:,None])-z*np.sin(bank[:,None])
        rails=xy[:,None,:]+local_y[...,None]*normal[:,None,:]
        heights=(y[:,None]+half_gauge*sides)*np.sin(bank[:,None])+z*np.cos(bank[:,None])
        return {"station_m":s.tolist(),"center_xy":xy.tolist(),"heading_rad":h.tolist(),"bank_rad":bank.tolist(),
                "curvature_m_inv":k.tolist(),"rails":np.concatenate((rails,heights[...,None]),axis=-1).tolist()}


def body_acceleration(speed,curvature,bank):
    centrifugal=speed**2*curvature
    return (-centrifugal*np.cos(bank)-G*np.sin(bank),
            centrifugal*np.sin(bank)-G*np.cos(bank))
