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
  scene.environmentIntensity = .75;
  scene.environmentRotation.x = Math.PI / 2;

  const key = new THREE.DirectionalLight(0xfff0df, 2.35);
  key.position.set(2.8, -3.6, 5.6);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  Object.assign(key.shadow.camera, {left: -3.5, right: 3.5, top: 2.9, bottom: -2.9, near: .5, far: 13});
  key.shadow.normalBias = .018;
  key.shadow.bias = -.0002;
  key.shadow.intensity = .75;
  key.shadow.radius = 2;
  const fill = new THREE.DirectionalLight(0xa7cbff, .84);
  fill.position.set(-3, 4, 3);
  const rim = new THREE.DirectionalLight(0xd9e5ff, 1.35);
  rim.position.set(-5, -2, 2);
  scene.add(key, fill, rim, new THREE.HemisphereLight(0xd7e8ff, 0x596a79, .77));

  const platform = new THREE.Group();
  const base = new THREE.Mesh(new THREE.BoxGeometry(4.5, 2.63, .12),
    new THREE.MeshStandardMaterial({color: 0x273342, roughness: .77, metalness: .18}));
  base.position.z = -.276;
  base.receiveShadow = true;
  const plinth = new THREE.Mesh(new THREE.BoxGeometry(4.39, 2.53, .037),
    new THREE.MeshStandardMaterial({color: 0x172533, roughness: .52, metalness: .30}));
  plinth.position.z = -.351;
  platform.add(base, plinth);
  scene.add(platform);
  return {platform, dispose() {
    environment.dispose();
    key.shadow.dispose();
  }};
}
