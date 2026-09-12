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
  const roomIds=await page.locator("#from option").evaluateAll(options=>options.map(option=>option.value));
  await page.evaluate(ids=>{for(const start of ids)for(const destination of ids){if(start===destination)continue;from.value=start;to.value=destination;accessible.checked=false;go.click();if(!result.textContent.includes("Walkspace verified"))throw new Error(`Route ${start} -> ${destination} left the walkspace graph`)}},roomIds);
  console.log(`Verified ${roomIds.length*(roomIds.length-1)} walkable routes at ${name} viewport.`);
  await page.selectOption("#from","WEH-5130");await page.selectOption("#to","WEH-4325");await page.click("#go");
  const standardMode=await page.locator(".route-meta span").nth(1).textContent();
  await page.check("#accessible");await page.click("#go");await page.click('[data-floor="4"]');
  await page.waitForTimeout(500);
  const heading=await page.locator(".route-head h2").textContent();
  const accessibleMode=await page.locator(".route-meta span").nth(1).textContent();
  const directions=await page.locator(".directions").textContent();
  const facilityLabels=await page.locator("#scene div").allTextContents();
  if(standardMode!=="Stairs"||accessibleMode!=="Elevator")throw new Error("Route recommendation mode is incorrect");
  if(!directions.includes("Exit the elevator")||!facilityLabels.some(x=>x.includes("ESCALATOR")))throw new Error("Vertical circulation UI is incomplete");
  const pixels=await page.locator("canvas").evaluate(canvas=>{
    const gl=canvas.getContext("webgl2")||canvas.getContext("webgl");
    const data=new Uint8Array(canvas.width*canvas.height*4);gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,data);
    const colors=new Set();for(let i=0;i<data.length;i+=400)colors.add(`${data[i]},${data[i+1]},${data[i+2]}`);
    return {sampledColors:colors.size,nonzero:data.some(value=>value!==0)};
  });
  if(!pixels.nonzero||pixels.sampledColors<4)throw new Error(`Blank 3D canvas at ${name} viewport`);
  await page.screenshot({path:`/tmp/campus-route-${name}-verified.png`,fullPage:true});
  console.log(JSON.stringify({name,canvas,heading,standardMode,accessibleMode,pixels,errors}));
}
await browser.close();
if(server)await new Promise(resolve=>server.close(resolve));
