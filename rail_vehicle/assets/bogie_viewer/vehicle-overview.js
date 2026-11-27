import * as THREE from "three";
import {OrbitControls} from "./vendor/OrbitControls.js";

const X = new THREE.Vector3(1, 0, 0);
const Y = new THREE.Vector3(0, 1, 0);
const views = {"整体视图":[.55,-1,.52], "侧视图":[0,-1,.05], "正视图":[1,-.001,.05],
  "俯视图":[0,-.001,1], "底视图":[0,-.001,-1]};

export function vehicleOverview(canvas, invalidate, environment, dimensionsSpec) {
  const scene = new THREE.Scene();
  scene.environment = environment;
  scene.environmentIntensity = .65;
  const camera = new THREE.OrthographicCamera(-15, 15, 8, -8, .05, 150);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.minZoom = .55;
  controls.maxZoom = 6;
  controls.enabled = false;
  controls.addEventListener("change", invalidate);
  const materials = {};
  for (const [name, color, metalness, roughness] of [
    ["paint",0x476988,.38,.43], ["roof",0x748492,.5,.55], ["glass",0x162737,.22,.31],
    ["frame",0x264c63,.4,.52], ["steel",0x818c95,.66,.36], ["spring",0x56797e,.48,.5],
    ["rubber",0x20292e,.02,.95], ["rail",0x657686,.56,.56], ["tie",0x414f58,.1,.88],
    ["accent",0x74a4b0,.25,.53],
  ]) materials[name] = new THREE.MeshStandardMaterial({color, metalness, roughness});
  scene.add(new THREE.HemisphereLight(0xd1e7ff, 0x47525b, 2.1));
  const key = new THREE.DirectionalLight(0xffebd9, 2.4);
  key.position.set(5, -7, 10);
  scene.add(key);

  const boxGeometry = new THREE.BoxGeometry(1, 1, 1);
  const {tread_radius:tread, flange_radius:flange, wheel_centres:wheelsAcross, axlebox_centres:boxesAcross,
    side_beam_centres:beamSpan, frame_length:frameLength, side_beam_width:beamWidth} = dimensionsSpec;
  const wheelCenter = wheelsAcross/2, boxCenter = boxesAcross/2, beamCenter = beamSpan/2;
  const wheelGeometry = new THREE.CylinderGeometry(tread, tread, .145, 32);
  const flangeGeometry = new THREE.CylinderGeometry(flange, flange, .024, 32);
  const lineGeometry = new THREE.CylinderGeometry(1, 1, 1, 12);
  const bodySurface = new THREE.Shape();
  bodySurface.moveTo(-.97,-1.40); bodySurface.lineTo(.97,-1.40);
  bodySurface.lineTo(1,-1.06); bodySurface.lineTo(1,1.06); bodySurface.lineTo(.97,1.40);
  bodySurface.lineTo(-.97,1.40); bodySurface.lineTo(-1,1.06); bodySurface.lineTo(-1,-1.06);
  bodySurface.closePath();
  const shellGeometry = new THREE.ExtrudeGeometry(bodySurface, {
    depth:1.78, bevelEnabled:true, bevelSize:.07, bevelThickness:.07, bevelSegments:2, steps:1,
  });
  const body = new THREE.Group();
  scene.add(body);
  function mesh(root, geometry, material, x=0, y=0, z=0) {
    const object = new THREE.Mesh(geometry, materials[material]);
    object.position.set(x, y, z);
    root.add(object);
    return object;
  }
  function box(root, material, shape, x=0, y=0, z=0) {
    const object = mesh(root, boxGeometry, material, x, y, z);
    object.scale.set(...shape);
    return object;
  }
  const shell = mesh(body, shellGeometry, "paint", 0, 0, -.76);
  const roof = box(body, "roof", [1,2.62,.09], 0,0,1.04);
  const skirt = box(body, "glass", [1,2.52,.16], 0,0,-.88);
  const stripes = [], windows = [];
  for (const side of [-1,1]) {
    stripes.push(box(body, "accent", [1,.02,.07],0,side*1.475,-.12));
    for (let i=0;i<10;i++) windows.push(box(body,"glass",[1,.021,.40],0,side*1.475,.43));
  }
  for (const side of [-1,1]) box(body,"glass",[.022,1.48,.42],side*10,0,.43).userData.end = side;
  const shellMaterials = new Map(body.children.map(piece=>[piece,piece.material]));
  const translucent = new Map(Object.values(materials).map(material=>[material,material.clone()]));
  for (const material of translucent.values()) {
    material.transparent = true;
    material.opacity = .18;
    material.depthWrite = false;
  }

  const ties = new THREE.InstancedMesh(boxGeometry, materials.tie, 45);
  scene.add(ties);
  const railHead = .142;
  const rails = [-1,1].map(side => box(scene, "rail", [30,.10,.14], 0, side*wheelCenter, railHead-.07));
  const bogies = [[1,0],[-1,1]].map(([sign,i]) => {
    const frame = new THREE.Group();
    frame.rotation.order = "ZYX";
    scene.add(frame);
    for (const side of [-1,1]) box(frame, "frame", [frameLength,beamWidth,.27], 0, side*beamCenter, .03);
    box(frame, "frame", [.54,beamSpan,.24]);
    const frameMat = materials.frame.clone();
    for (const piece of frame.children) piece.material = frameMat;
    const secondary = [-1,1].map(side => ({
      side, bellows:mesh(scene, lineGeometry, "rubber"), collar:mesh(scene, lineGeometry, "steel"),
      rollLink:mesh(scene, lineGeometry, "accent"),
    }));
    box(frame,"steel",[.08,1.85,.08],-.46,0,.11);
    for (const side of [-1,1]) box(frame,"steel",[.25,.065,.065],-.36,side*.91,.11);
    const wheels = [[1,0],[-1,1]].map(([end,j]) => {
      const carrier = new THREE.Group(), axle = new THREE.Group();
      scene.add(carrier);
      carrier.add(axle);
      box(axle,"steel",[.115,boxesAcross+.1,.115]);
      const springs = [-1,1].map(side => {
        mesh(axle,wheelGeometry,"steel",0,side*wheelCenter);
        mesh(axle,flangeGeometry,"rail",0,side*(wheelCenter-.084));
        box(axle,"accent",[.12,.023,.05],.26,side*(wheelCenter+.079),0);
        box(carrier,"frame",[.29,.22,.26],0,side*boxCenter,.02);
        return {side, spring:mesh(scene,lineGeometry,"spring"), damper:mesh(scene,lineGeometry,"steel")};
      });
      return {carrier, axle, end, springs, j:2*i+j};
    });
    return {frame, frameMat, wheels, secondary, sign, i};
  });
  let lastHalf, aspect = 1;
  const from = new THREE.Vector3(), to = new THREE.Vector3(), direction = new THREE.Vector3();
  function link(object, radius, start, end, portion=1) {
    const length = direction.subVectors(end, start).length();
    object.position.copy(start).lerp(end, portion/2);
    object.quaternion.setFromUnitVectors(Y, direction.multiplyScalar(1/length));
    object.scale.set(radius, length*portion, radius);
  }
  function dimensions(half) {
    if (half === lastHalf) return;
    lastHalf = half;
    const length = half+2.6;
    shell.scale.x = length;
    roof.scale.x = 2*length*.963;
    skirt.scale.x = 2*length*.963;
    for (const stripe of stripes) stripe.scale.x = 2*length*.945;
    windows.forEach((window,i) => {
      window.position.x = (((i%10)+.5)/5-1)*length*.94;
      window.scale.x = length*.153;
    });
    body.children.filter(item => item.userData.end).forEach(item => {
      item.position.x = length*.965*item.userData.end;
    });
    const trackLength = 2*(length+2.3);
    for (const rail of rails) rail.scale.x = trackLength;
    const tie = new THREE.Object3D();
    tie.scale.set(.23, boxesAcross+.24, .1);
    for (let i=0;i<ties.count;i++) {
      tie.position.set((i/(ties.count-1)-.5)*trackLength, 0, -.04);
      tie.updateMatrix();
      ties.setMatrixAt(i, tie.matrix);
    }
    ties.instanceMatrix.needsUpdate = true;
    ties.computeBoundingSphere();
    reset();
  }
  function resize(width,height) {
    if (!height) return;
    aspect = width/height;
    const extent = (lastHalf ?? 8.5)+4.0;
    const vertical = Math.max(2.85, extent/aspect, extent*.35+1.2);
    camera.left = -vertical*aspect; camera.right = vertical*aspect;
    camera.top = vertical; camera.bottom = -vertical;
    camera.updateProjectionMatrix();
  }
  function reset(view="整体视图") {
    controls.enableDamping = false;
    controls.update();
    controls.target.set(0,0,1.35);
    camera.position.fromArray(views[view] ?? views["整体视图"]).normalize().multiplyScalar(42).add(controls.target);
    camera.zoom = 1;
    resize(aspect,1);
    controls.update();
    controls.enableDamping = true;
    invalidate();
  }
  const low = new THREE.Color(0x2f74b4), high = new THREE.Color(0xe99446);
  return {
    controls, camera, resize, reset,
    setShellTransparency(value) {
      for (const [piece, original] of shellMaterials) piece.material = value ? translucent.get(original) : original;
      invalidate();
    },
    draw(renderer, {q,y,gain,half,span,travel}, wheelAngle, heat=false, cameraSource=null) {
      dimensions(half);
      if (cameraSource) {
        camera.position.copy(cameraSource.camera.position);
        camera.zoom = cameraSource.camera.zoom;
        camera.updateProjectionMatrix();
        controls.target.copy(cameraSource.controls.target);
      }
      body.position.set(0,q[10]*gain,2.48+q[0]*gain);
      body.rotation.set(q[11]*gain,-q[1]*gain,q[12]*gain,"ZYX");
      body.updateMatrixWorld();
      for (const {frame,frameMat,wheels,secondary,sign,i} of bogies) {
        frameMat.color.copy(heat && travel ? low : materials.frame.color);
        if (heat && travel) frameMat.color.lerp(high, Math.min(1,Math.abs(travel[i])/.02));
        const center = sign*half, f=2+2*i, lateral=13+i*3;
        frame.position.set(center,q[lateral]*gain,.95+q[f]*gain);
        frame.rotation.set(q[lateral+1]*gain,-q[f+1]*gain,q[lateral+2]*gain);
        frame.updateMatrixWorld();
        for (const {side,bellows,collar,rollLink} of secondary) {
          frame.localToWorld(from.set(0,side*beamCenter,.12));
          body.localToWorld(to.set(center,side*beamCenter,-.96));
          link(bellows,.19,from,to);
          link(collar,.205,from,to,.12);
          frame.localToWorld(from.set(-.235,side*.91,.11));
          body.localToWorld(to.set(center-.235,side*.91,-.96));
          link(rollLink,.028,from,to);
        }
        for (const {carrier,axle,end,springs,j} of wheels) {
          carrier.position.set(center+end*span, y[j]*gain,railHead+tread+q[6+j]*gain);
          axle.rotation.y = wheelAngle;
          for (const {side,spring,damper} of springs) {
            from.set(carrier.position.x,carrier.position.y+side*boxCenter,carrier.position.z+.10);
            frame.localToWorld(to.set(end*span,side*boxCenter,-.05));
            link(spring,.088,from,to);
            from.x += end*.23;
            frame.localToWorld(to.set(end*(span+.13),side*(boxCenter+.04),-.015));
            link(damper,.042,from,to);
          }
        }
      }
      controls.update();
      renderer.render(scene,camera);
    },
    dispose() {
      controls.dispose();
      for (const geometry of [boxGeometry,wheelGeometry,flangeGeometry,lineGeometry,shellGeometry]) geometry.dispose();
      for (const material of Object.values(materials)) material.dispose();
      for (const material of translucent.values()) material.dispose();
      for (const {frameMat} of bogies) frameMat.dispose();
      ties.dispose();
      scene.clear();
    },
  };
}
