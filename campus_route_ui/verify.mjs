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
  const canvas=await page.locator("canvas").boundingBox();
  const roomCount=await page.locator("#room-options option").count();
  if(roomCount!==1262)throw new Error(`Expected 1262 mapped spaces, found ${roomCount}`);
  const initialStatus=await page.locator("#scene-status").textContent(),wallCount=Number(initialStatus.match(/([\d,]+) wall segments/)?.[1].replaceAll(",","")||0);
  if(wallCount<100)throw new Error(`Expected recognized 3D walls, found ${wallCount}`);
  if(name==="desktop"){
    await page.screenshot({path:"/tmp/campus-route-recognized-walls.png"});
    await page.selectOption("#floor-filter","WEH-4");
    const inspectionStyle=await page.addStyleTag({content:".room-label{display:none!important}"});
    await page.waitForTimeout(150);
    await page.screenshot({path:"/tmp/campus-route-recognized-walls-floor.png"});
    await inspectionStyle.evaluate(element=>element.remove());
    await page.selectOption("#floor-filter","all");
  }
  async function route(start,end,isAccessible=false){await page.fill("#from",start);await page.fill("#to",end);await page.setChecked("#accessible",isAccessible);await page.click("#go");await page.waitForFunction(()=>!document.querySelector("#go").disabled);return page.locator(".directions").textContent()}
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
  await page.waitForTimeout(500);
  const heading=await page.locator(".route-head h2").textContent();
  const directions=await page.locator(".directions").textContent();
  const facilityLabels=await page.locator("#scene div").allTextContents();
  if(standardMode!=="Stairs"||accessibleMode!=="Elevator")throw new Error("Route recommendation mode is incorrect");
  if(!standard.includes("Use the stairs")||!accessibleRoute.includes("Take the elevator")||!facilityLabels.some(x=>x.includes("ESCALATOR")))throw new Error("Vertical circulation UI is incomplete");
  const pixels=await page.locator("canvas").evaluate(canvas=>{
    const gl=canvas.getContext("webgl2")||canvas.getContext("webgl");
    const data=new Uint8Array(canvas.width*canvas.height*4);gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,data);
    const colors=new Set();for(let i=0;i<data.length;i+=400)colors.add(`${data[i]},${data[i+1]},${data[i+2]}`);
    return {sampledColors:colors.size,nonzero:data.some(value=>value!==0)};
  });
  if(!pixels.nonzero||pixels.sampledColors<4)throw new Error(`Blank 3D canvas at ${name} viewport`);
  await page.screenshot({path:`/tmp/campus-route-${name}-verified.png`,fullPage:true});
  console.log(JSON.stringify({name,canvas,roomCount,wallCount,heading,standardMode,accessibleMode,pixels,errors}));
}
await browser.close();
if(server)await new Promise(resolve=>server.close(resolve));
