import * as THREE from "three";

// Illustrative local metres, X forward, Y left, Z up. Solver pose remains the only motion input.
export const vehicleAssembly = Object.freeze({
  shell: {width:2.92,roof:1.16,floor:-.84},
  secondarySeat: {lateral:1.16,vertical:-.90},
  links: [
    {id:"traction_link",kind:"traction",upper:[.58,0,-.90],lower:[-.42,0,.17],radius:.044},
    {id:"lateral_damper",kind:"damper",upper:[.35,.56,-.90],lower:[.35,-.54,.21],radius:.048},
    {id:"yaw_damper",kind:"damper",upper:[.82,1.38,-.78],lower:[-.80,1.19,.21],radius:.051},
    {id:"antiroll_link",kind:"bar",upper:[-.58,1.04,-.86],lower:[-.53,1.00,.22],radius:.027},
  ],
});

export function skinInstances(root,geometry,material,locations,own) {
  const model=own(new THREE.InstancedMesh(geometry,material,locations.length));
  const transform=new THREE.Object3D();
  locations.forEach(({at,scale},i)=>{
    transform.position.fromArray(at);
    transform.scale.fromArray(scale);
    transform.updateMatrix();
    model.setMatrixAt(i,transform.matrix);
  });
  model.computeBoundingSphere();
  root.add(model);
  return model;
}

export function addCarbodyDetails(root,span,spacing,put,cube,materials,own) {
  const glazing=[];
  const half=vehicleAssembly.shell.width/2, floor=vehicleAssembly.shell.floor;
  for(const side of [-1,1]){
    for(let i=0;i<9;i++)glazing.push({at:[(i-4)*span/5,side*(half-.003),.51],scale:[span*.145,.026,.43]});
    glazing.push({at:[side*(span+.067),0,.47],scale:[.025,1.79,.42]});
    put(root,cube,"bodyEdge",[0,side*1.46,-.23],[2*span-1.41,.021,.067]);
    put(root,cube,"frame",[0,side*1.29,floor+.01],[2*span-1.13,.12,.20]);
  }
  skinInstances(root,cube,materials.glass,glazing,own);
  for(const station of [-spacing,spacing]){
    put(root,cube,"frame",[station,0,-.80],[.68,2.70,.18]);
    put(root,cube,"steel",[station,0,-.92],[.25,.32,.13]);
    for(const side of [-1,1])put(root,cube,"steel",[station,side*vehicleAssembly.secondarySeat.lateral,-.875],[.46,.47,.065]);
  }
  for(const sign of [-1,1]){
    put(root,cube,"frame",[sign*(span-.18),0,-.75],[.42,1.89,.16]);
    put(root,cube,"steel",[sign*(span+.10),0,-.80],[.41,.19,.16]);
  }
}
