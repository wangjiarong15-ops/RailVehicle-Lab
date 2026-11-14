import * as THREE from "three";
import {RoomEnvironment} from "./vendor/RoomEnvironment.js";

// Color, metalness and roughness for each surface on a mechanical assembly.
const finishes = {
  "构架": [["#34475a", .29, .42], ["#527087", .37, .34], ["#222f3c", .44, .47]],
  "轮对": [["#494d53", .84, .31], ["#879198", .92, .22], ["#30383e", .76, .40]],
  "一系悬挂": [["#9ba5a9", .76, .26], ["#8999a2", .66, .30], ["#3f535f", .46, .44]],
  "二系悬挂": [["#4b626f", .50, .40], ["#85949c", .65, .37], ["#292f37", .03, .61]],
  "轴箱": [["#5f696e", .64, .40], ["#89979f", .74, .32], ["#35434d", .53, .48]],
  "电机与传动": [["#455c61", .57, .37], ["#778c93", .64, .34], ["#596a70", .52, .43]],
  "制动装置": [["#725445", .48, .39], ["#929aa2", .84, .28], ["#4a4a4a", .62, .40]],
  "轨道参照": [["#687583", .78, .31], ["#414d57", .58, .44], ["#495057", .02, .89]],
  focus: [["#287ed7", .28, .34], ["#77bfff", .39, .25], ["#285987", .43, .41]],
  alert: [["#de7828", .25, .39], ["#ffb859", .37, .28], ["#a54620", .27, .42]],
};

export function materials() {
  return Object.fromEntries(Object.entries(finishes).map(([part, values]) => [
    part, values.map(([color, metalness, roughness]) =>
      new THREE.MeshStandardMaterial({color, metalness, roughness})),
  ]));
}

export function studio(scene, renderer) {
  const generator = new THREE.PMREMGenerator(renderer);
  const room = new RoomEnvironment();
  const environment = generator.fromScene(room, .05);
  room.dispose();
  generator.dispose();
  scene.environment = environment.texture;
  scene.environmentIntensity = .62;
  scene.environmentRotation.x = Math.PI / 2;

  const key = new THREE.DirectionalLight(0xfff3e6, 2.25);
  key.position.set(2.8, -3.6, 5.93);
  key.target.position.z = .33;
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.normalBias = .018;
  key.shadow.bias = -.00045;
  key.shadow.intensity = .82;
  key.shadow.radius = 2.8;
  const fill = new THREE.DirectionalLight(0xb8d1f1, .63);
  fill.position.set(-3, 4, 3);
  const rim = new THREE.DirectionalLight(0xd9e5ff, 1.20);
  rim.position.set(-5, -2, 2);
  const ambient = new THREE.HemisphereLight(0xe0edff, 0x737c87, .65);
  ambient.position.set(0, 0, 1); // The workbench uses Z-up, unlike the Three.js default.
  scene.add(key, key.target, fill, rim, ambient);

  function fitShadows(exploded) {
    // Light-space bounds cover the track and moving assembly. Only the exploded view needs the larger area.
    const camera = key.shadow.camera;
    Object.assign(camera, exploded
      ? {left: -3.20, right: 2.70, top: 3.30, bottom: -2.65, near: 3.20, far: 10}
      : {left: -2.80, right: 2.55, top: 2.48, bottom: -2.22, near: 4, far: 9.6});
    camera.updateProjectionMatrix();
  }

  const platform = new THREE.Group();
  const base = new THREE.Mesh(new THREE.BoxGeometry(4.5, 2.63, .12),
    new THREE.MeshStandardMaterial({color: 0x202b36, roughness: .88, metalness: .08}));
  base.position.z = -.276;
  base.receiveShadow = true;
  const plinth = new THREE.Mesh(new THREE.BoxGeometry(4.39, 2.53, .037),
    new THREE.MeshStandardMaterial({color: 0x172533, roughness: .52, metalness: .30}));
  plinth.position.z = -.351;
  platform.add(base, plinth);
  scene.add(platform);
  return {platform, fitShadows, dispose() {
    environment.dispose();
    key.shadow.dispose();
  }};
}
