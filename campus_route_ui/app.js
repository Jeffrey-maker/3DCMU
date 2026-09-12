import * as THREE from "three";
import {OrbitControls} from "three/addons/controls/OrbitControls.js";
import {CSS2DRenderer, CSS2DObject} from "three/addons/renderers/CSS2DRenderer.js";

const rooms={
  "WEH-5222":{name:"Wean 5222",p:[5.8,5,0.1]},"WEH-5343":{name:"Wean 5343",p:[12.3,5,1.1]},
  "WEH-5130":{name:"Wean 5130",p:[3.4,5,-1.2]},"WEH-4325":{name:"Wean 4325",p:[12.1,4,1.1]},
  "SH-4N120A":{name:"Scott 4N120A",p:[-8.8,4,-2.2]},"SH-4N103":{name:"Scott 4N103",p:[-4.8,4,-1.8]},
  "SH-4S417":{name:"Scott 4S417",p:[-0.3,4,5.1]}
};
const instructions={
  "WEH-5222|WEH-5343":["Exit room 5222 into the west corridor.","Follow the corridor east toward rooms 5200 and 5008.","Continue east into corridor 5300.","Follow the east corridor to room 5343."],
  "SH-4N120A|SH-4S417":["Exit 4N120A into corridor 4N000.","Travel south to the junction near 4S000/4S100.","Continue into the long south corridor.","Continue past the 4S200 and 4S400 room groups to 4S417."],
  "SH-4N103|WEH-4325":["Exit 4N103 into corridor 4N000.","Follow signs for the Wean Hall passage.","Cross the enclosed passage into Wean Hall.","Enter Wean central corridor 4000.","Continue east into corridor 4300.","Follow corridor 4300 to room 4325."],
  "WEH-5130|WEH-4325":["Exit room 5130 into the west corridor.","Go to the west stair beside room 5129.","Descend one floor to Level 4.","Exit the stair into corridor 4100.","Continue east to central corridor 4000.","Continue east into corridor 4300.","Follow corridor 4300 to room 4325."],
  "WEH-5222|SH-4S417":["Exit room 5222 into the west corridor.","Go to the west stair beside room 5129.","Descend one floor to Level 4.","Exit the stair into corridor 4100.","Continue east to central corridor 4000.","Follow signs for Scott Hall Level 4.","Cross the enclosed passage into Scott Hall.","Enter Scott's north corridor 4N000.","Travel south to the junction near 4S000/4S100.","Continue into the long south corridor.","Continue past the 4S200 and 4S400 room groups to 4S417."]
};

const floorY=f=>(f-4)*1.35;
const toVector=([x,f,z])=>new THREE.Vector3(x,floorY(f)+.28,z);
const routePoints={
  "WEH-5222|WEH-5343":[[5.8,5,.1],[7.2,5,0],[9.4,5,.2],[11.2,5,.5],[12.3,5,1.1]],
  "SH-4N120A|SH-4S417":[[-8.8,4,-2.2],[-6.2,4,-1.7],[-3.2,4,-.9],[-1.7,4,1.3],[-.8,4,3.3],[-.3,4,5.1]],
  "SH-4N103|WEH-4325":[[-4.8,4,-1.8],[-3.1,4,-.9],[-1.2,4,-.2],[1.2,4,0],[4.5,4,.2],[8,4,.6],[12.1,4,1.1]],
  "WEH-5130|WEH-4325":[[3.4,5,-1.2],[5.5,5,-.4],[6.6,5,0],[6.6,4,0],[8.4,4,.3],[10.4,4,.7],[12.1,4,1.1]],
  "WEH-5222|SH-4S417":[[5.8,5,.1],[6.6,5,0],[6.6,4,0],[4.3,4,.1],[1.2,4,0],[-1.2,4,-.2],[-3.2,4,-.9],[-1.7,4,1.3],[-.8,4,3.3],[-.3,4,5.1]]
};
const accessibleRoutePoints={
  "WEH-5130|WEH-4325":[[3.4,5,-1.2],[5.5,5,-.4],[7.5,5,.4],[7.5,4,.4],[8.4,4,.3],[10.4,4,.7],[12.1,4,1.1]],
  "WEH-5222|SH-4S417":[[5.8,5,.1],[7.5,5,.4],[7.5,4,.4],[4.3,4,.1],[1.2,4,0],[-1.2,4,-.2],[-3.2,4,-.9],[-1.7,4,1.3],[-.8,4,3.3],[-.3,4,5.1]]
};

const container=document.querySelector("#scene");
const scene=new THREE.Scene();
scene.background=new THREE.Color(0xdde9e5);
scene.fog=new THREE.Fog(0xdde9e5,28,48);
const camera=new THREE.PerspectiveCamera(43,1,.1,100);
const renderer=new THREE.WebGLRenderer({antialias:true,powerPreference:"high-performance",preserveDrawingBuffer:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;container.appendChild(renderer.domElement);
const labels=new CSS2DRenderer();labels.domElement.style.position="absolute";labels.domElement.style.inset="0";labels.domElement.style.pointerEvents="none";container.appendChild(labels.domElement);
const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=.07;controls.minDistance=10;controls.maxDistance=37;controls.maxPolarAngle=Math.PI*.48;
scene.add(new THREE.HemisphereLight(0xffffff,0x739188,2.2));const sun=new THREE.DirectionalLight(0xffffff,2.7);sun.position.set(-10,18,9);sun.castShadow=true;scene.add(sun);
const ground=new THREE.Mesh(new THREE.PlaneGeometry(42,28),new THREE.MeshStandardMaterial({color:0xc4d6d0,roughness:1}));ground.rotation.x=-Math.PI/2;ground.position.y=-.25;ground.receiveShadow=true;scene.add(ground);
const grid=new THREE.GridHelper(42,28,0x94afa8,0xb9cbc6);grid.position.y=-.23;scene.add(grid);

const floors=[];
function box(group,x,z,w,d,h,color,y=.1){const m=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),new THREE.MeshStandardMaterial({color,roughness:.76,metalness:.04,transparent:true,opacity:.92}));m.position.set(x,y,z);m.castShadow=true;m.receiveShadow=true;group.add(m);return m}
function outline(group,x,z,w,d,y,color){const points=[[-w/2,-d/2],[w/2,-d/2],[w/2,d/2],[-w/2,d/2],[-w/2,-d/2]].map(([a,b])=>new THREE.Vector3(x+a,y,z+b));group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color})))}
function addLabel(group,text,x,z){const el=document.createElement("div");el.textContent=text;el.style.cssText="background:#172126dd;color:white;padding:3px 6px;border-radius:3px;font:700 9px -apple-system,sans-serif;white-space:nowrap";const label=new CSS2DObject(el);label.position.set(x,.42,z);group.add(label)}
function makeFloor(building,floor){const g=new THREE.Group();g.userData={building,floor};g.position.y=floorY(floor);const scott=building==="Scott";const color=scott?0x8bc1b4:0xe2c578;const line=scott?0x286e6b:0x966a24;if(scott){box(g,-6,-1,8.2,3.2,.22,color);box(g,-1.9,2.15,3.0,8.7,.22,color);outline(g,-6,-1,8.2,3.2,.24,line);outline(g,-1.9,2.15,3,8.7,.24,line);for(let x=-9;x<-2.5;x+=1.2)box(g,x,-1.85,.8,.55,.38,0xc5ded7,.35)}else{box(g,7,0,12.2,3.7,.22,color);box(g,10.6,2.25,4.4,2,.22,color);outline(g,7,0,12.2,3.7,.24,line);outline(g,10.6,2.25,4.4,2,.24,line);for(let x=2;x<12.5;x+=1.35)box(g,x,-1.25,.85,.55,.38,0xf1dfa9,.35)}addLabel(g,`${building} · L${floor}`,scott?-6:7,scott?-1:0);scene.add(g);floors.push(g)}
[3,4,5,6].forEach(f=>makeFloor("Scott",f));[2,3,4,5,6].forEach(f=>makeFloor("Wean",f));
const bridge=box(scene,0,0,3.8,.75,.32,0xd8e0de,floorY(4)+.15);bridge.material.opacity=1;

const facilities=new THREE.Group();scene.add(facilities);
function facilityLabel(text,x,y,z,color){const el=document.createElement("div");el.textContent=text;el.style.cssText=`background:${color};color:white;padding:3px 6px;border-radius:3px;font:800 9px -apple-system,sans-serif;white-space:nowrap`;const label=new CSS2DObject(el);label.position.set(x,y,z);facilities.add(label)}
function addStairs(){const group=new THREE.Group();const steps=9;for(let i=0;i<steps;i++){const step=new THREE.Mesh(new THREE.BoxGeometry(.78,.12,.25),new THREE.MeshStandardMaterial({color:0x536a70,roughness:.75}));step.position.set(6.15,floorY(4)+.12+i*.15,-.65+i*.12);step.castShadow=true;group.add(step)}facilityLabel("STAIRS",5.25,floorY(5)+.55,-.8,"#455b61");facilities.add(group);return group}
function addElevator(){const group=new THREE.Group();const shaft=new THREE.Mesh(new THREE.BoxGeometry(1,5.8,1),new THREE.MeshStandardMaterial({color:0x6f98a3,transparent:true,opacity:.22,roughness:.3,metalness:.35}));shaft.position.set(7.5,2.2,.4);group.add(shaft);const cabin=new THREE.Mesh(new THREE.BoxGeometry(.78,.62,.78),new THREE.MeshStandardMaterial({color:0x087f82,metalness:.45,roughness:.3}));cabin.position.set(7.5,floorY(4)+.45,.4);cabin.userData.elevator=true;group.add(cabin);facilityLabel("ELEVATOR",7.5,floorY(5)+.9,.65,"#087f82");facilities.add(group);return {group,cabin}}
function addEscalator(){const group=new THREE.Group();for(let i=0;i<11;i++){const step=new THREE.Mesh(new THREE.BoxGeometry(.68,.08,.2),new THREE.MeshStandardMaterial({color:i%2?0xc99d35:0xe2c36d,metalness:.3,roughness:.45}));step.position.set(8.75,floorY(4)+.12+i*.125,-.55+i*.11);step.userData.phase=i/11;group.add(step)}const railMaterial=new THREE.MeshStandardMaterial({color:0x765f2d,metalness:.5});for(const x of [8.35,9.15]){const rail=new THREE.Mesh(new THREE.BoxGeometry(.05,1.72,.05),railMaterial);rail.position.set(x,.78,.02);rail.rotation.x=Math.PI*.25;group.add(rail)}facilityLabel("ESCALATOR",9.8,floorY(5)+.55,.95,"#9a7223");facilities.add(group);return group}
const stairs=addStairs(),elevator=addElevator(),escalator=addEscalator();

let routeGroup=null,routeLine=null,autoRotate=false;
function marker(position,color){const group=new THREE.Group();const pin=new THREE.Mesh(new THREE.ConeGeometry(.22,.65,18),new THREE.MeshStandardMaterial({color}));pin.position.y=.38;pin.rotation.x=Math.PI;const ring=new THREE.Mesh(new THREE.TorusGeometry(.27,.06,8,24),new THREE.MeshBasicMaterial({color}));ring.rotation.x=Math.PI/2;group.add(pin,ring);group.position.copy(position);return group}
function drawRoute(start,end,useElevator=false){if(routeGroup)scene.remove(routeGroup);routeGroup=new THREE.Group();const key=`${start}|${end}`;const raw=(useElevator&&accessibleRoutePoints[key])||routePoints[key]||[rooms[start].p,rooms[end].p];const points=raw.map(toVector);const curve=new THREE.CatmullRomCurve3(points,false,"centripetal");const geometry=new THREE.BufferGeometry().setFromPoints(curve.getPoints(100));const material=new THREE.LineDashedMaterial({color:0xe65f48,dashSize:.35,gapSize:.18,linewidth:3});routeLine=new THREE.Line(geometry,material);routeLine.computeLineDistances();routeGroup.add(routeLine,marker(points[0],0xe65f48),marker(points.at(-1),0x087f82));scene.add(routeGroup);stairs.traverse(o=>{if(o.material)o.material.emissive?.set(useElevator?0:0x332008)});elevator.cabin.material.emissive.set(useElevator?0x0b4f4f:0);const mid=curve.getPoint(.5);controls.target.copy(mid);camera.position.set(mid.x+12,mid.y+12,mid.z+15);controls.update();document.querySelector("#scene-status").textContent=`${rooms[start].name} → ${rooms[end].name}`}
function resetCamera(){camera.position.set(20,18,24);controls.target.set(1,1,0);controls.update()}
function resize(){const w=container.clientWidth,h=container.clientHeight;camera.aspect=w/h;camera.updateProjectionMatrix();renderer.setSize(w,h,false);labels.setSize(w,h)}
function animate(time=0){requestAnimationFrame(animate);if(autoRotate)scene.rotation.y+=.0025;if(routeLine)routeLine.material.dashOffset-=.012;elevator.cabin.position.y=floorY(4)+.45+(Math.sin(time*.001)+1)*.675;escalator.children.forEach(step=>{if(step.userData.phase!==undefined)step.material.emissive.setScalar(.04+.08*Math.sin(time*.004-step.userData.phase*6.28))});controls.update();renderer.render(scene,camera);labels.render(scene,camera)}
resetCamera();resize();animate();addEventListener("resize",resize);

const from=document.querySelector("#from"),to=document.querySelector("#to"),result=document.querySelector("#result"),accessible=document.querySelector("#accessible");
Object.entries(rooms).forEach(([id,r])=>{from.add(new Option(r.name,id));to.add(new Option(r.name,id))});from.value="SH-4N103";to.value="WEH-4325";
function show(){const a=from.value,b=to.value;if(a===b){result.className="directions show";result.innerHTML="<div class='route-head'><h2>You are already there</h2></div>";drawRoute(a,b);return}const crossFloor=rooms[a].p[1]!==rooms[b].p[1],crossBuilding=a.slice(0,2)!==b.slice(0,2),useElevator=accessible.checked&&crossFloor;let mode=crossBuilding&&!crossFloor?"Level 4 passage":crossFloor?(useElevator?"Elevator":"Stairs"):"Direct corridor";let steps=instructions[`${a}|${b}`]||["Walk to the nearest mapped corridor.","Follow the highlighted route to the destination."];if(useElevator)steps=steps.map(x=>x.includes("Descend one floor")?"Take the elevator down to Level 4.":x.includes("west stair")?"Go to the elevator beside the central core.":x.includes("Exit the stair")?"Exit the elevator into corridor 4000.":x);result.className="directions show";result.innerHTML=`<div class="route-head"><h2>${rooms[a].name} → ${rooms[b].name}</h2><span>${steps.length} steps</span></div><div class="route-meta"><span class="recommended">RECOMMENDED</span><span>${mode}</span>${crossFloor&&!useElevator?"<span>Elevator available</span>":""}${crossFloor?"<span>Escalator direction unverified</span>":""}</div><ol>${steps.map((x,i)=>`<li><b>${i+1}</b><span>${x}</span></li>`).join("")}</ol>`;drawRoute(a,b,useElevator)}
document.querySelector("#go").onclick=show;document.querySelector("#swap").onclick=()=>{const x=from.value;from.value=to.value;to.value=x;show()};document.querySelector("#reset-view").onclick=resetCamera;document.querySelector("#rotate").onclick=e=>{autoRotate=!autoRotate;e.currentTarget.classList.toggle("active",autoRotate)};
document.querySelectorAll("[data-floor]").forEach(button=>button.onclick=()=>{document.querySelectorAll("[data-floor]").forEach(b=>b.classList.remove("active"));button.classList.add("active");const selected=button.dataset.floor;floors.forEach(g=>g.visible=selected==="all"||String(g.userData.floor)===selected);bridge.visible=selected==="all"||selected==="4";facilities.visible=selected==="all"||selected==="4"||selected==="5"});
document.querySelectorAll(".examples button").forEach(button=>button.onclick=()=>{from.value=button.dataset.from;to.value=button.dataset.to;show()});show();
