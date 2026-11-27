"""Constant-matrix second-order systems, with zero displacement and velocity at t=0."""

import numpy as np


def newmark_response(mass, damping, stiffness, force, dt, stride):
    """Average acceleration (β=1/4, γ=1/2); return samples at every stride."""
    dofs = len(mass)
    inverse = np.linalg.solve(mass + dt/2*damping + dt**2/4*stiffness, np.eye(dofs))
    q = np.zeros(dofs)
    dq = q.copy()
    ddq = np.linalg.solve(mass, force[0])
    output = np.empty(((len(force)-1)//stride+1, 3, dofs))
    output[0] = q, dq, ddq
    for i in range(1, len(force)):
        qp = q + dt*dq + dt**2/4*ddq
        vp = dq + dt/2*ddq
        ddq = inverse @ (force[i] - damping@vp - stiffness@qp)
        q = qp + dt**2/4*ddq
        dq = vp + dt/2*ddq
        if i % stride == 0:
            output[i//stride] = q, dq, ddq
    return output.transpose(1, 0, 2)
