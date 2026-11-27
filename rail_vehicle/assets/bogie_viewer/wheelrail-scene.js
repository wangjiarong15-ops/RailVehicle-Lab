import * as THREE from "three";
import {OrbitControls} from "./vendor/OrbitControls.js";

const Y=new THREE.Vector3(0,1,0);
const UP=new THREE.Vector3(0,0,1);

export function wheelRailFrameParts(detailed) {
  const ids=["side_beam_l","side_beam_r","cross_members","motor_a","motor_b"];
  if (!detailed || ids.some(id=>!detailed.parts.has(id))) return [];
  // Take the validated GLB in its rest pose before the separate inspection scene moves it.
  return ids.flatMap(id=>detailed.parts.get(id).meshes.map(mesh=>({
    geometry:mesh.geometry,matrix:mesh.matrixWorld.clone(),paint:id.startsWith("motor")?"motor":"frame",
  })));
}

export function wheelRailScene(canvas, invalidate, environment, route, dims, frameParts) {
  const scene=new THREE.Scene();
  scene.environment=environment;
  scene.environmentIntensity=.66;
  scene.add(new THREE.HemisphereLight(0xcee4fc,0x4e5868,2));
  const key=new THREE.DirectionalLight(0xffead1,2.2);
  key.position.set(4,-9,13);
  scene.add(key);
  const camera=new THREE.OrthographicCamera(-18,18,8,-8,.05,240);
  camera.up.copy(UP);
  const controls=new OrbitControls(camera,canvas);
  controls.enableDamping=true;
  controls.minZoom=.5;
  controls.maxZoom=6;
  controls.addEventListener("change",invalidate);
  const assets=new Set();
  const own=resource=>{assets.add(resource);return resource;};
  const materials=Object.fromEntries([
    ["steel",0x8194a6,.68],["frame",0x39586d,.44],["cabin",0x64829b,.46],["glass",0x1d2e41,.18],
    ["bodyEdge",0x8ab0bc,.52],["coil",0x5a8b8e,.5],["bellows",0x273742,.19],["tie",0x465264,.24],
    ["motor",0x597079,.42],
    ["focus",0x1faed6,.45],
  ].map(([k,color,metalness])=>[k,own(new THREE.MeshStandardMaterial({color,metalness,roughness:.48}))]));
  const cube=own(new THREE.BoxGeometry(1,1,1));
  const tube=own(new THREE.CylinderGeometry(1,1,1,12));
  const tread=own(new THREE.CylinderGeometry(dims.radius,dims.radius,.135,36));
  const flange=own(new THREE.CylinderGeometry(dims.radius+.027,dims.radius+.027,.028,36));
  const ring=own(new THREE.CylinderGeometry(dims.radius*.58,dims.radius*.58,.035,24));
  class CoilPath extends THREE.Curve {
    getPoint(t,target=new THREE.Vector3()) {
      const angle=t*14*Math.PI;
      return target.set(.86*Math.cos(angle),t-.50,.86*Math.sin(angle));
    }
  }
  const coil=own(new THREE.TubeGeometry(new CoilPath(),70,.10,5));
  const bellows=own(new THREE.LatheGeometry([
    [.72,-.5],[1.,-.43],[1.05,-.25],[.88,-.10],[1.05,.08],[1.,.31],[.80,.5]
  ].map(([radius,height])=>new THREE.Vector2(radius,height)),20));
  const dummy=new THREE.Object3D(), from=new THREE.Vector3(),to=new THREE.Vector3(),direction=new THREE.Vector3();
  function put(parent,geometry,colour,at=[0,0,0],size=[1,1,1]) {
    const mesh=new THREE.Mesh(geometry,materials[colour]);
    mesh.position.fromArray(at); mesh.scale.fromArray(size);
    parent.add(mesh); return mesh;
  }
  const z=route.rails;
  for (const side of [0,1]) {
    const beams=new THREE.InstancedMesh(cube,materials.steel,z.length-1);
    let prev=new THREE.Vector3(),next=new THREE.Vector3();
    z.slice(1).forEach(([l,r],i)=>{
      prev.fromArray(z[i][side]);next.fromArray(side? r:l);
      direction.subVectors(next,prev);
      dummy.position.copy(prev).lerp(next,.5).addScaledVector(UP,-.055);
      dummy.quaternion.setFromUnitVectors(Y,direction.clone().normalize());
      dummy.scale.set(.094,direction.length()+.003,.105);
      dummy.updateMatrix();beams.setMatrixAt(i,dummy.matrix);
    });
    scene.add(beams);
    own(beams);
  }
  const ties=new THREE.InstancedMesh(cube,materials.tie,Math.ceil(z.length/4));
  for(let i=0;i<ties.count;i++){
    const [l,r]=z[4*i];
    from.fromArray(l);to.fromArray(r);
    dummy.position.copy(from).lerp(to,.5).addScaledVector(UP,-.16);
    dummy.quaternion.setFromUnitVectors(Y,direction.subVectors(to,from).normalize());
    dummy.scale.set(.24,2*dims.half_gauge+.53,.13);dummy.updateMatrix();ties.setMatrixAt(i,dummy.matrix);
  }
  scene.add(ties);own(ties);
  const roots=Array.from({length:7},()=>new THREE.Group());
  scene.add(...roots);
  const span=dims.bogie_half_spacing+2.5;
  const hull=new THREE.Shape();
  hull.moveTo(-span,-1.07);hull.lineTo(-span+.62,-1.39);hull.lineTo(span-.62,-1.39);hull.lineTo(span,-1.07);
  hull.lineTo(span,1.07);hull.lineTo(span-.62,1.39);hull.lineTo(-span+.62,1.39);hull.lineTo(-span,1.07);
  hull.closePath();
  put(roots[0],own(new THREE.ExtrudeGeometry(hull,{depth:1.70,bevelEnabled:true,bevelThickness:.08,
    bevelSize:.065,bevelSegments:2,steps:1})),"cabin",[0,0,-.69]);
  put(roots[0],cube,"bodyEdge",[0,0,1.07],[2*span-1.36,2.43,.09]);
  put(roots[0],cube,"glass",[0,0,-.78],[2*span,2.72,.12]);
  for(const sign of [-1,1]){
    for(let i=0;i<9;i++)put(roots[0],cube,"glass",[(i-4)*span/5,sign*1.457,.51],[span*.145,.026,.43]);
    put(roots[0],cube,"bodyEdge",[0,sign*1.46,-.23],[2*span-1.41,.021,.067]);
    put(roots[0],cube,"glass",[sign*(span+.067),0,.47],[.025,1.79,.42]);
  }
  for(const root of roots.slice(1,3)) {
    if (frameParts.length) {
      for (const part of frameParts) {
        // Geometries are shared with the validated inspection model and owned by that model.
        const mesh=put(root,part.geometry,part.paint);
        mesh.matrix.copy(part.matrix);
        mesh.matrix.elements[14]-=.95;
        mesh.matrixAutoUpdate=false;
      }
    } else {
      for (const sign of [-1,1]) put(root,cube,"frame",[0,sign*1.16,.04],[3.55,.25,.30]);
      put(root,cube,"frame",[0,0,.10],[.63,2.15,.28]);
    }
  }
  const spins=roots.slice(3).map(root=>{
    const node=new THREE.Group();root.add(node);
    put(node,tube,"steel",[0,0,0],[.067,2*dims.half_gauge+.56,.067]);
    for(const sign of [-1,1]){
      put(node,tread,"steel",[0,sign*dims.half_gauge,0]);
      put(node,flange,"frame",[0,sign*(dims.half_gauge-.076),0]);
      put(node,ring,"steel",[0,sign*(dims.half_gauge-.26),0]);
      put(node,tube,"steel",[0,sign*(dims.half_gauge+.085),0],[.128,.085,.128]);
      put(node,cube,"bodyEdge",[.24,sign*(dims.half_gauge+.072),0],[.105,.025,.053]);
      put(root,tube,"frame",[0,sign*1.20,.02],[.158,.23,.158]);
      put(root,cube,"motor",[0,sign*(dims.half_gauge-.26),dims.radius*.53],[.14,.090,.135]);
    }
    return node;
  });
  const links=[];
  for(const [high,low,station,secondary] of [[0,1,dims.bogie_half_spacing,true],[0,2,-dims.bogie_half_spacing,true],
      [1,3,dims.half_wheelbase,false],[1,4,-dims.half_wheelbase,false],[2,5,dims.half_wheelbase,false],[2,6,-dims.half_wheelbase,false]]){
    for(const sign of [1,-1]){
      links.push({high,low,station,sign,secondary,
        spring:put(scene,secondary?bellows:coil,secondary?"bellows":"coil"),damper:put(scene,tube,"steel")});
    }
  }
  const marks=Array.from({length:8},()=>{
    const normal=new THREE.ArrowHelper(UP,from,1,0x6be2a6,.15,.07);
    const tangent=new THREE.ArrowHelper(UP,from,1,0xffba70,.10,.055);
    scene.add(normal,tangent);
    return {normal,tangent};
  });
  function link(obj,lengthScale) {
    const length=direction.subVectors(to,from).length();
    obj.position.copy(from).lerp(to,.5);
    obj.quaternion.setFromUnitVectors(Y,direction.multiplyScalar(1/Math.max(.001,length)));
    obj.scale.set(lengthScale,length,lengthScale);
  }
  const presets={
    vehicle:{root:0,height:4.5,vertical:0}, front_frame:{root:1,height:1.85,vertical:.12},
    rear_frame:{root:2,height:1.85,vertical:.12},front_wheel:{root:3,height:1.45,vertical:0},
    front_primary:{root:1,height:1.4,vertical:-.16},front_secondary:{root:1,height:1.35,vertical:.42},
  };
  let aspect=1, focus="vehicle", centre=new THREE.Vector3(), chosenView="整体视图";
  const offsets={"整体视图":[13,-26,12],"侧视图":[0,-26,1.2],"正视图":[24,-.08,2.1],
    "俯视图":[0,-.03,30],"底视图":[0,-.02,-26]};
  const pos=new THREE.Vector3();
  function resize(w,h){
    if(!h)return;
    aspect=w/h;
    const height=focus==="vehicle"?Math.max(4.5,span*1.18/aspect):Math.max(presets[focus].height,2.45/aspect);
    camera.left=-height*aspect; camera.right=height*aspect;camera.top=height;camera.bottom=-height;
    camera.updateProjectionMatrix();
  }
  function reset(view=chosenView){
    chosenView=view;
    controls.enableDamping=false;
    controls.update();
    controls.target.copy(centre);
    pos.fromArray(offsets[view]??offsets["整体视图"]).multiplyScalar(focus==="vehicle"?1:.35);
    camera.position.copy(centre).add(pos);
    camera.zoom=1;resize(aspect,1);
    controls.update();
    controls.enableDamping=true;
    invalidate();
  }
  reset();
  const old=new THREE.Vector3(),newCentre=new THREE.Vector3(),tan=new THREE.Vector3(),long=new THREE.Vector3();
  let pendingFocus=false;
  function project(data,index,angle,forces,source=null){
    const p=data.pose[index];
    for(const [i,root] of roots.entries()){
      const [x,y,z,roll,pitch,yaw]=p[i];
      root.position.set(x,y,z);
      root.rotation.set(roll,-pitch,yaw,"ZYX");
      root.updateMatrixWorld();
    }
    for(const spin of spins)spin.rotation.y=angle;
    for(const {high,low,station,sign,secondary,spring,damper} of links){
      roots[high].localToWorld(to.set(station,sign*1.16,secondary?-.90:-.10));
      roots[low].localToWorld(from.set(0,sign*(secondary ? 1.16 : 1.20),secondary ? .17 : .14));
      link(spring,secondary ? .19 : .081);
      to.x+=.15;to.z+=.03;from.x+=.20;
      link(damper,secondary ? .024 : .037);
    }
    const normals=data.tread_normal_n[index], fx=data.tread_longitudinal_n[index],fy=data.tread_lateral_n[index];
    const contact=data.contact[index], heading=data.contact_heading[index],bank=data.contact_bank[index];
    for(let i=0;i<marks.length;i++){
      const {normal,tangent}=marks[i], wheel=i>>1,side=i&1,h=heading[wheel],theta=bank[wheel];
      const q=normals[wheel][side];
      normal.visible=tangent.visible=forces&&q>0;
      if(!normal.visible)continue;
      normal.position.fromArray(contact[wheel][side]);tangent.position.copy(normal.position);
      tan.set(-Math.sin(h)*Math.cos(theta),Math.cos(h)*Math.cos(theta),Math.sin(theta));
      long.set(Math.cos(h),Math.sin(h),0);
      direction.crossVectors(long,tan).normalize();
      normal.setDirection(direction);normal.setLength(q/50000,.13,.065);
      direction.copy(long).multiplyScalar(fx[wheel][side]).addScaledVector(tan,fy[wheel][side]);
      const strength=direction.length();
      tangent.visible=strength>50;
      if(tangent.visible){tangent.setDirection(direction.normalize());tangent.setLength(strength/15000,.07,.04);}
    }
    if(source){
      camera.position.copy(source.camera.position);
      camera.zoom=source.camera.zoom;
      controls.target.copy(source.controls.target);camera.updateProjectionMatrix();
    } else {
      const preset=presets[focus];
      old.copy(centre);newCentre.copy(roots[preset.root].position);
      if(focus==="vehicle") newCentre.z=1.45;
      else newCentre.z+=preset.vertical;
      centre.copy(newCentre);
      if(pendingFocus){pendingFocus=false;reset();}
      else{
        pos.subVectors(centre,old);
        camera.position.add(pos);controls.target.add(pos);
      }
    }
    controls.update();
  }
  return {controls,camera,resize,reset,
    setShell(hidden){roots[0].visible=!hidden;},
    focus(value){
      focus=presets[value]?value:"vehicle";
      for(const item of links){
        const selected=focus==="front_primary"&&item.high===1 ||
          focus==="front_secondary"&&item.secondary&&item.low===1;
        item.spring.material=selected?materials.focus:materials[item.secondary?"bellows":"coil"];
        item.damper.material=selected?materials.focus:materials.steel;
      }
      pendingFocus=true;resize(aspect,1);invalidate();
    },
    draw(renderer,data,index,angle,forces,source){project(data,index,angle,forces,source);renderer.render(scene,camera);},
    dispose(){
      controls.dispose();
      for(const {normal,tangent} of marks){normal.dispose();tangent.dispose();}
      for(const asset of assets)asset.dispose();
      scene.clear();
    }};
}
