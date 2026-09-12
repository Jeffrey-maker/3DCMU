import {chromium} from "playwright-core";
import {createServer} from "node:http";
import {readFile} from "node:fs/promises";
import {extname,join} from "node:path";

let server;
let baseUrl=process.env.BASE_URL;
if(!baseUrl){
  const types={".html":"text/html",".js":"text/javascript",".css":"text/css",".json":"application/json"};
  server=createServer(async(request,response)=>{try{const pathname=new URL(request.url,"http://localhost").pathname;const relative=pathname==="/"?"index.html":pathname.slice(1);const data=await readFile(join(process.cwd(),relative));response.writeHead(200,{"Content-Type":types[extname(relative)]||"application/octet-stream"});response.end(data)}catch{response.writeHead(404);response.end("Not found")}});
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  baseUrl=`http://127.0.0.1:${server.address().port}/`;
}
const browser=await chromium.launch({headless:true,executablePath:"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"});
for(const [name,viewport] of Object.entries({phone:{width:390,height:844},desktop:{width:1000,height:900}})){
  console.log(`Verifying ${name} viewport...`);
  const page=await browser.newPage({viewport,deviceScaleFactor:1});
  const errors=[];page.on("console",m=>{if(m.type()==="error")errors.push(m.text())});page.on("pageerror",e=>errors.push(e.message));
  await page.goto(baseUrl,{waitUntil:"networkidle"});
  await page.waitForSelector("canvas");
  await page.waitForTimeout(200);
  if(errors.length)throw new Error(`Browser initialization failed: ${errors.join("; ")}`);
  const appFrame=await page.locator(".app").boundingBox();
  if(Math.round(appFrame.width)!==390)throw new Error(`Expected a 390px phone frame, found ${appFrame.width}px at ${name} viewport`);
  if(name==="desktop"&&Math.round(appFrame.height)!==844)throw new Error(`Expected an 844px phone frame on desktop, found ${appFrame.height}px`);
  const canvas=await page.locator("canvas").boundingBox();
  const roomCount=await page.locator("#room-options option").count();
  if(roomCount!==1262)throw new Error(`Expected 1262 mapped spaces, found ${roomCount}`);
  const initialStatus=await page.locator("#scene-status").textContent(),wallCount=Number(initialStatus.match(/([\d,]+) walls/)?.[1].replaceAll(",","")||0),spaceCount=Number(initialStatus.match(/([\d,]+) spaces/)?.[1].replaceAll(",","")||0),referencePassageCount=Number(initialStatus.match(/([\d,]+) YBC passages/)?.[1].replaceAll(",","")||0);
  if(wallCount<100)throw new Error(`Expected recognized 3D walls, found ${wallCount}`);
  if(spaceCount<100)throw new Error(`Expected classified floor spaces, found ${spaceCount}`);
  if(referencePassageCount!==818)throw new Error(`Expected 818 ybc reference passages, found ${referencePassageCount}`);
  await page.selectOption("#floor-filter","WEH-4");await page.waitForTimeout(100);
  const visibleRoomLabels=()=>page.locator(".room-label").evaluateAll(elements=>elements.filter(element=>getComputedStyle(element).display!=="none").length);
  if(await visibleRoomLabels()===0)throw new Error("Room numbers were not shown when enabled");
  await page.setChecked("#room-numbers",false);await page.waitForTimeout(100);
  if(await visibleRoomLabels()!==0)throw new Error("Room numbers remained visible after disabling them");
  await page.setChecked("#room-numbers",true);await page.selectOption("#floor-filter","all");
  if(name==="desktop"){
    await page.screenshot({path:"/tmp/campus-route-recognized-walls.png"});
    await page.selectOption("#floor-filter","WEH-4");
    const inspectionStyle=await page.addStyleTag({content:".room-label{display:none!important}"});
    await page.waitForTimeout(150);
    await page.screenshot({path:"/tmp/campus-route-recognized-walls-floor.png"});
    await inspectionStyle.evaluate(element=>element.remove());
    await page.selectOption("#floor-filter","all");
  }
  async function route(start,end,isAccessible=false){if(await page.locator("#result.expanded").count())await page.click("#sheet-handle");await page.fill("#from",start);await page.fill("#to",end);await page.setChecked("#accessible",isAccessible);await page.click("#go");await page.waitForFunction(()=>!document.querySelector("#go").disabled);return page.locator(".directions").textContent()}
  const sameFloor=await route("Wean 5130","Wean 5434");
  if(!sameFloor.includes("Map walkspace"))throw new Error("Same-floor path did not use the plan raster");
  const standard=await route("Wean 5130","Wean 4325");
  const standardMode=await page.locator(".route-meta span").nth(1).textContent();
  const accessibleRoute=await route("Wean 5130","Wean 4325",true);
  const accessibleMode=await page.locator(".route-meta span").nth(1).textContent();
  const crossBuilding=await route("Scott 4N103","Wean 4325",true);
  if(!crossBuilding.includes("Bridge required"))throw new Error("Cross-building route did not use the Level 4 bridge");
  const multiFloorCrossBuilding=await route("Scott 3101","Wean 5130",true);
  if(!multiFloorCrossBuilding.includes("Bridge required")||!multiFloorCrossBuilding.includes("Take the elevator"))throw new Error("Multi-floor cross-building route bypassed a required connector");
  if(name==="phone")await route("Wean 5130","Wean 4325",true);await page.selectOption("#floor-filter","WEH-4");
  await page.waitForTimeout(300);const sheet=page.locator("#result"),handle=page.locator("#sheet-handle"),sheetBox=await sheet.boundingBox(),handleBox=await handle.boundingBox();
  await page.mouse.move(handleBox.x+handleBox.width/2,handleBox.y+handleBox.height/2);await page.mouse.down();await page.mouse.move(handleBox.x+handleBox.width/2,handleBox.y+sheetBox.height*.72,{steps:5});await page.mouse.up();await page.waitForTimeout(300);
  if(await sheet.evaluate(element=>element.classList.contains("expanded")))throw new Error(`Directions sheet did not slide down at ${name} viewport`);
  const collapsedHandleBox=await handle.boundingBox();await page.mouse.move(collapsedHandleBox.x+collapsedHandleBox.width/2,collapsedHandleBox.y+collapsedHandleBox.height/2);await page.mouse.down();await page.mouse.move(collapsedHandleBox.x+collapsedHandleBox.width/2,collapsedHandleBox.y-sheetBox.height*.72,{steps:5});await page.mouse.up();await page.waitForTimeout(300);
  if(!await sheet.evaluate(element=>element.classList.contains("expanded")))throw new Error(`Directions sheet did not slide up at ${name} viewport`);
  const stepCount=await page.locator(".direction-step").count(),nodeCount=await page.locator(".route-node-label").count();
  if(stepCount<3||nodeCount!==stepCount)throw new Error(`Expected one 3D node per direction at ${name} viewport, found ${nodeCount} nodes for ${stepCount} steps`);
  await page.locator(".direction-step").nth(2).click();await page.waitForTimeout(700);
  if(await sheet.evaluate(element=>element.classList.contains("expanded")))throw new Error(`Directions sheet did not collapse after selecting a step at ${name} viewport`);
  if(!await page.locator(".direction-step").nth(2).evaluate(element=>element.classList.contains("active")))throw new Error(`Selected direction was not highlighted at ${name} viewport`);
  if(await page.locator("#floor-filter").inputValue()==="all")throw new Error(`Step selection did not reveal its floor at ${name} viewport`);
  if(!(await page.locator("#scene-status").textContent()).startsWith("Step 3 ·"))throw new Error(`Step selection did not update map status at ${name} viewport`);
  const beforeTurn=await page.locator("canvas").screenshot(),turnBox=await page.locator("canvas").boundingBox();await page.mouse.move(turnBox.x+turnBox.width*.55,turnBox.y+turnBox.height*.5);await page.mouse.down();await page.mouse.move(turnBox.x+turnBox.width*.72,turnBox.y+turnBox.height*.55,{steps:5});await page.mouse.up();await page.waitForTimeout(400);const afterTurn=await page.locator("canvas").screenshot();
  if(Buffer.compare(beforeTurn,afterTurn)===0)throw new Error(`3D surroundings did not rotate after focusing a step at ${name} viewport`);
  const heading=await page.locator(".route-head h2").textContent();
  const directions=await page.locator(".directions").textContent();
  const facilityLabels=await page.locator("#scene div").allTextContents();
  if(standardMode!=="Stairs"||accessibleMode!=="Elevator")throw new Error("Route recommendation mode is incorrect");
  if(!standard.includes("Use the stairs")||!accessibleRoute.includes("Take the elevator"))throw new Error("Vertical circulation UI is incomplete");
  if(facilityLabels.filter(text=>text==="STAIRS 1"||text==="STAIRS 2").length!==4)throw new Error("Expected two staircase stacks in each building");
  if(facilityLabels.some(text=>text.includes("ESCALATOR")))throw new Error("The map must not render escalators");
  const pixels=await page.locator("canvas").evaluate(canvas=>{
    const gl=canvas.getContext("webgl2")||canvas.getContext("webgl");
    const data=new Uint8Array(canvas.width*canvas.height*4);gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,data);
    const colors=new Set();let routePixels=0;for(let i=0;i<data.length;i+=4){if(i%400===0)colors.add(`${data[i]},${data[i+1]},${data[i+2]}`);if(data[i]>210&&data[i+1]<100&&data[i+2]<100)routePixels++}
    return {sampledColors:colors.size,nonzero:data.some(value=>value!==0),routePixels};
  });
  if(!pixels.nonzero||pixels.sampledColors<4)throw new Error(`Blank 3D canvas at ${name} viewport`);
  if(pixels.routePixels<50)throw new Error(`Expected a visible thick red route at ${name} viewport, found ${pixels.routePixels} red pixels`);
  await page.screenshot({path:`/tmp/campus-route-${name}-verified.png`,fullPage:true});
  console.log(JSON.stringify({name,appFrame,canvas,roomCount,wallCount,spaceCount,referencePassageCount,heading,stepCount,nodeCount,standardMode,accessibleMode,pixels,errors}));
}
await browser.close();
if(server)await new Promise(resolve=>server.close(resolve));
