import fs from "node:fs/promises";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const OUT = "/Users/nbb/Desktop/2-Posdoc_Heng/0-Codex/3-Transport/docs/Transport_NH_GUI_User_Manual_20260828.pptx";
const RENDER = "/Users/nbb/Desktop/2-Posdoc_Heng/0-Codex/3-Transport/docs/Transport_NH_GUI_User_Manual_20260828/build/rendered";
const SRC = "/Users/nbb/Desktop/2-Posdoc_Heng/0-Codex/3-Transport/Magneto_Electro_Optical_Microwave_Transport_NH_dev.py";
const PROJECT = "/Users/nbb/Desktop/2-Posdoc_Heng/0-Codex/Codex-Memory/20-Projects/P11-PYTHON-TRANSPORT.md";
const ICON = "/Users/nbb/Desktop/2-Posdoc_Heng/0-Codex/3-Transport/assets/Magneto_Electro_Optical_Microwave_Transport_NH_icon.png";
const IMG_6221 = "/var/folders/61/zwljyfb1529g_99j7w44y9200000gn/T/codex-clipboard-b3786815-319a-47c4-bb19-e5b277d5ca81.png";
const IMG_STFMR = "/var/folders/61/zwljyfb1529g_99j7w44y9200000gn/T/codex-clipboard-8b467c84-bfc9-43a1-8ef8-d0df7ef0ee66.png";
const IMG_STFMR_LAYOUT = "/var/folders/61/zwljyfb1529g_99j7w44y9200000gn/T/codex-clipboard-aa2be826-b768-47f5-8cab-141e9505b813.png";

async function pngDataUrl(path) {
  return `data:image/png;base64,${(await fs.readFile(path)).toString("base64")}`;
}
const ICON_DATA = await pngDataUrl(ICON);
const IMG_6221_DATA = await pngDataUrl(IMG_6221);
const IMG_STFMR_DATA = await pngDataUrl(IMG_STFMR);
const IMG_STFMR_LAYOUT_DATA = await pngDataUrl(IMG_STFMR_LAYOUT);

const C = {
  navy: "#16324F", teal: "#008D8D", cyan: "#27B7C4", ink: "#14212B",
  muted: "#5F6B74", light: "#F4F7F8", line: "#D8E0E5", white: "#FFFFFF",
  amber: "#D97706", red: "#C62828", green: "#159447", paleTeal: "#E8F5F4",
  paleAmber: "#FFF6E6", paleRed: "#FDECEC", paleBlue: "#EDF4F9",
};
const FONT = "Arial";

function addBox(slide, left, top, width, height, fill=C.white, line=C.line, radius="rounded-xl") {
  return slide.shapes.add({
    geometry: "roundRect",
    position: { left, top, width, height },
    fill,
    line: { style: "solid", fill: line, width: 1 },
    borderRadius: radius,
  });
}

function addText(slide, text, left, top, width, height, opts={}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position: { left, top, width, height },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    fontFamily: FONT,
    fontSize: opts.fontSize ?? 18,
    bold: opts.bold ?? false,
    color: opts.color ?? C.ink,
    alignment: opts.align ?? "left",
    verticalAlignment: opts.valign ?? "middle",
  };
  return shape;
}

function notes(slide, ranges, extra=[]) {
  const lines = ["[Sources]", ...ranges.map(r => `- ${SRC}:${r}`), ...extra.map(x => `- ${x}`)];
  slide.speakerNotes.textFrame.setText(lines.join("\n"));
  slide.speakerNotes.setVisible(true);
}

function baseSlide(pres, title, section="User Guide") {
  const slide = pres.slides.add();
  slide.background.fill = C.light;
  slide.shapes.add({ geometry: "rect", position: { left: 0, top: 0, width: 1280, height: 9 }, fill: C.teal, line: { style: "solid", fill: C.teal, width: 0 } });
  addText(slide, section.toUpperCase(), 54, 28, 280, 24, { fontSize: 13, bold: true, color: C.teal });
  addText(slide, title, 54, 56, 1172, 52, { fontSize: 36, bold: true, color: C.navy });
  return slide;
}

function footer(slide, n) {
  addText(slide, "Transport_NH · GUI User Guide", 54, 687, 520, 20, { fontSize: 12, color: C.muted });
  addText(slide, String(n).padStart(2, "0"), 1170, 687, 56, 20, { fontSize: 12, bold: true, color: C.teal, align: "right" });
}

function addItemsColumn(slide, heading, items, left, top, width, height, accent=C.teal) {
  addText(slide, heading, left, top, width, 28, { fontSize: 22, bold: true, color: C.navy });
  slide.shapes.add({ geometry: "rect", position: { left, top: top+33, width: 46, height: 4 }, fill: accent, line: { style: "solid", fill: accent, width: 0 } });
  const available = height - 48;
  const rowH = Math.min(88, available / Math.max(1, items.length));
  let y = top + 48;
  for (const item of items) {
    addText(slide, item[0], left, y, width, 24, { fontSize: 17, bold: true, color: item[2] ?? C.ink, valign: "top" });
    addText(slide, item[1], left, y+24, width, Math.max(34, rowH-24), { fontSize: 16, color: C.muted, valign: "top" });
    y += rowH;
  }
}

function listSlide(pres, title, leftHead, leftItems, rightHead, rightItems, ranges, section="Module Operation") {
  const extraSources = Array.isArray(section) ? section : [];
  if (Array.isArray(section)) section = "Module Operation";
  const slide = baseSlide(pres, title, section);
  addBox(slide, 46, 124, 580, 544, C.white);
  addBox(slide, 654, 124, 580, 544, C.white);
  addItemsColumn(slide, leftHead, leftItems, 70, 146, 532, 500, C.teal);
  addItemsColumn(slide, rightHead, rightItems, 678, 146, 532, 500, C.cyan);
  notes(slide, ranges, extraSources);
  return slide;
}

function screenshotSlide(pres, title, imageData, imagePath, caption, ranges, extraSources=[], section="Interface Tour") {
  const slide = baseSlide(pres, title, section);
  addBox(slide, 54, 128, 1172, 500, C.white);
  slide.images.add({ dataUrl: imageData, alt: caption, fit: "contain", position: { left: 72, top: 144, width: 1136, height: 454 } });
  addText(slide, caption, 72, 610, 1136, 34, { fontSize: 16, color: C.muted, align: "center" });
  notes(slide, ranges, [`${imagePath} (current GUI screenshot)`, ...extraSources]);
  return slide;
}

function matrixSlide(pres) {
  const slide = baseSlide(pres, "Nine Tabs: From Optical Imaging to Microwave Transport", "System Navigation");
  const tabs = [
    ["MOKE Light Source", "Camera, four-channel LED, analyzer rotation stage"],
    ["MOKE Motor", "Cell‑VSDM displacement and Home"],
    ["MOKE Magnet", "Differential imaging, magnetic field, current sweep"],
    ["2410", "Resistance monitoring, voltage sweep, ROI"],
    ["6221", "AC/DC, finite pulses, pulse sweep"],
    ["IV", "6221 + multiple voltage readouts"],
    ["Gate‑VI", "2410 pulsed gating + 6221/voltage readout"],
    ["PPMS1", "Environment control + 6221/SR830 field sweep"],
    ["STFMR", "FMR sweeps with 4886/475/PNA"],
  ];
  const xs = [58, 458, 858], ys = [142, 302, 462];
  tabs.forEach((t, i) => {
    const x=xs[i%3], y=ys[Math.floor(i/3)];
    addBox(slide, x, y, 364, 124, i===2 || i===8 ? C.paleTeal : C.white, i===2 || i===8 ? C.teal : C.line);
    addText(slide, t[0], x+20, y+16, 324, 32, { fontSize: 24, bold: true, color: C.navy });
    addText(slide, t[1], x+20, y+52, 324, 56, { fontSize: 16, color: C.muted, valign: "top" });
  });
  notes(slide, ["11020-11028", "11050-12130", "13854-16270", "29018-29190"]);
  return slide;
}

function matrixSafetySlide(pres) {
  const slide = baseSlide(pres, "Shared Interaction Rules: Colors, Enabling and Dangerous Actions", "Safety and Status");
  const rows = [
    ["Gray dot / Not connected", "Resource not yet opened; related actions are usually disabled."],
    ["Green dot / Connected", "Only means a software session is established; not equivalent to completed hardware acceptance."],
    ["Gray button", "Not allowed in the current state, or missing a connection or safety confirmation."],
    ["Red button", "Stop, output off, zero or emergency action; highest priority."],
    ["Safety confirmation checkbox", "Confirm wiring, polarity, limits, cooling, mechanical path and personnel safety."],
    ["Connect → Parameters → Output", "Connect and read status first, then set small-range parameters, and only then allow output."],
    ["Save", "Prefer saving after the sweep ends; synchronized images add significant disk and processing load."],
    ["Exit program", "The program tries to close outputs and sessions; still verify the instrument front panels."],
  ];
  addBox(slide, 54, 126, 1172, 534, C.white);
  rows.forEach((r,i)=>{
    const y=146+i*62;
    slide.shapes.add({ geometry:"ellipse", position:{left:78,top:y+8,width:18,height:18}, fill: i===1?C.green:i===3?C.red:i===4?C.amber:"#9AA0A6", line:{style:"solid",fill:"none",width:0} });
    addText(slide,r[0],112,y,280,34,{fontSize:17,bold:true,color:C.navy});
    addText(slide,r[1],400,y,790,40,{fontSize:16,color:C.muted});
    if(i<rows.length-1) slide.shapes.add({geometry:"rect",position:{left:112,top:y+52,width:1078,height:1},fill:C.line,line:{style:"solid",fill:C.line,width:0}});
  });
  notes(slide,["10620-10990","11100-11120","11340-11370","11795-11835","14730-14770","15120-15150"]);
  return slide;
}

async function main() {
  await fs.mkdir(RENDER, { recursive: true });
  const pres = Presentation.create({ slideSize: { width: 1280, height: 720 } });

  // 1 Cover
  {
    const s=pres.slides.add(); s.background.fill=C.navy;
    s.images.add({dataUrl:ICON_DATA,alt:"Transport_NH application icon",fit:"contain",position:{left:72,top:76,width:140,height:140}});
    addText(s,"Magneto‑Electro‑Optical‑Microwave Transport",72,238,980,108,{fontSize:44,bold:true,color:C.white,valign:"top"});
    addText(s,"Transport_NH Complete GUI User Guide",76,376,900,44,{fontSize:28,bold:true,color:"#9FE6E1"});
    addText(s,"Based on the latest dev source of 2026‑08‑28 · Nine modules · Control-by-control description",76,438,980,34,{fontSize:18,color:"#D6E7EE"});
    addText(s,"NH & Prof Chen’s group",76,615,520,30,{fontSize:17,color:"#9FB4C2"});
    s.shapes.add({geometry:"rect",position:{left:1090,top:0,width:190,height:720},fill:C.teal,line:{style:"solid",fill:C.teal,width:0}});
    notes(s,["1-30","11020-11028"],[`${ICON} (project icon)`]);
  }

  listSlide(pres,"Scope of This Guide","Applicability",[
    ["Single source of truth","The real controls, defaults and callback logic in the current dev source are authoritative."],
    ["Coverage","Nine tabs, Camera Live, ROI, sweeps, saving, emergency stop and exit."],
    ["Training use","Suitable for self-study by new users before operating, lab handover and on-site verification."],
    ["Hardware boundary","Offline checks are not equivalent to acceptance on real camera, serial, GPIB, PPMS or Windows hardware."],
  ],"How to Read",[
    ["Modules first","Locate the tab first, then check the corresponding control table and operation sequence."],
    ["Then safety","Before any non-zero output, motion or sweep, hardware-side confirmation must be completed."],
    ["Saving last","Understand the differences between CSV, PNG, 16‑bit raw images, differential images and video."],
    ["Exception handling","Stop/zero first; do not use repeated reconnects instead of checking resource occupation or hardware state."],
  ],["1-30"],"Reading Guide");
  matrixSlide(pres);
  matrixSafetySlide(pres);

  listSlide(pres,"MOKE Light Source | Camera Entry","Camera Control",[
    ["Connect camera","Creates a PCO camera session and starts background acquisition; no acquisition before connecting."],
    ["Disconnect camera","Stops acquisition and releases the camera; the camera view and related buttons update with the state."],
    ["Show Camera Live window","Opens the standalone live view; does not reconnect the camera."],
    ["Collapse view","Hides the live window but keeps the established camera session."],
  ],"Role of Camera Live",[
    ["Live observation","Shows the current processed 8‑bit frame, corner markers, exposure and contrast state."],
    ["ROI entry","Shift + left-drag selects an ROI; plain left-drag pans."],
    ["Shared with MOKE","ROI and saving in MOKE, 2410 and 6221 all read the same camera data chain."],
    ["Rotation rule","Raw frames are first rotated 90° counter-clockwise; corner markers 1/2/3/4 rotate accordingly."],
  ],["11255-11303","8800-10100"]);

  listSlide(pres,"MOKE Light Source | LED Serial Port and Global Control","Connection Area",[
    ["LED serial port","Select the COM resource; the default link is COM5."],
    ["Refresh","Rescans the available serial ports."],
    ["Connect / Disconnect","Opens or releases the 115200‑8N1 serial session."],
    ["Show log / Hide log","Expands or collapses the TX/RX communication log."],
    ["Zero all / Emergency off","Sets all four channels to 0 and turns output off; use first in an emergency."],
  ],"Four-Channel Global Control",[
    ["0 / 10 / 50 / 100 / 500","Writes the same current preset into all four channel inputs."],
    ["All four continuous on","Switches all four channel modes to continuous output."],
    ["Apply all / Query all","Batch-writes or batch-reads the state of all four channels."],
    ["Set four-channel continuous current","Uses the input value as the continuous current of all four channels; upper limit 2000."],
    ["Auto read-back after set","Queries immediately after each write; useful for verification but increases traffic."],
    ["Show and edit advanced parameters","Expands the p/g/d/f advanced pulse parameters of each channel."],
  ],["11068-11224","16450-16550"]);

  listSlide(pres,"MOKE Light Source | Channels, ROI and Analyzer","Per-Channel LED",[
    ["Current","Enter the current value of this channel."],
    ["Mode","Select off, continuous or a pulse mode supported by the device."],
    ["Apply","Writes only the current channel."],
    ["Query","Reads only the current channel."],
    ["p/g/d/f","Edit pulse-related parameters in advanced mode."],
    ["ROI intensity vs time","Plots the raw 16‑bit mean intensity of the selected ROI, without background subtraction."],
  ],"Thorlabs K10CR1 Analyzer",[
    ["Connect / Disconnect","Establishes or releases the Kinesis rotation-stage session."],
    ["Read status / Identify device","Updates position and Home/Enabled state; triggers Identify."],
    ["Safety confirmation","Normal motion is blocked while unchecked."],
    ["Relative move / To absolute angle","Executes a relative angle or a 0–360° absolute angle respectively."],
    ["−Jog / +Jog","≤1° diagnostic jog; only this kind of motion is allowed before Home."],
    ["Home / Stop now","Establishes the mechanical reference; or stops motion immediately. Does not auto-zero on exit."],
  ],["11124-11223","13854-13980"]);

  listSlide(pres,"Camera Live | Mouse and Shortcuts","Mouse",[
    ["Left-drag","Pans the current view; the pan range is limited by the image boundary."],
    ["Shift + left-drag","Creates/reselects the ROI; reselection is blocked during a 2410 sweep."],
    ["Wheel","Zooms the view around the mouse position."],
    ["ROI clear shortcut","Clears the current ROI; the live curve returns to the waiting state."],
  ],"Keyboard State Control",[
    ["A · Contrast","Toggles auto/fixed contrast; auto contrast only uses the currently visible region."],
    ["B · BG on/off","Turns background subtraction on or off."],
    ["N · new BG","Re-acquires the background and restarts the ROI‑time axis from 0."],
    ["R · reset","Resets zoom and pan."],
    ["M · marker","Shows/hides the 100× field-of-view marker."],
    ["Q · quit","Closes the Camera Live window; not a substitute for the main program's safe exit."],
  ],["8800-10100","17000-18000"]);

  listSlide(pres,"MOKE Motor | Connection, Initialization and Status","Serial Port and Output",[
    ["Refresh / Connect / Disconnect","Selects and manages the 115200‑8N1, address 1 motor session."],
    ["Init ON","Initializes the Cell‑VSDM output."],
    ["Init OFF / Stop output","Turns initialization off and stops drive output."],
    ["Safety confirmation","Confirm mechanical path, limits and direction; continuous jogging does not auto-uncheck it."],
  ],"Status and Parameters",[
    ["Read once / Continuous read","Reads register 4 and the Home flag; they are not position feedback."],
    ["Direction Mode1","Displays the current direction mode."],
    ["Speed / Acceleration / Deceleration","Sets the rps and rps/s parameters."],
    ["Apply parameters","Writes only motion parameters; does not start a move."],
    ["Show communication log","Shows the Modbus TX/RX; click again to collapse."],
  ],["29018-29190"]);

  listSlide(pres,"MOKE Motor | Position, Step and Home","Position Actions",[
    ["Go to absolute position","Enter the target in µm and write it; confirm safety and motion direction first."],
    ["7250 µm","Quickly go to the fixed experiment position."],
    ["Home","Executes directly when safety confirmation is checked; only shows a reminder when unchecked."],
    ["Step size","Enter the µm increment of a single jog."],
    ["↑ Up / ↓ Down","Jogs continuously by the set step size, without a dialog."],
  ],"Understanding the Displayed Values",[
    ["Software commanded position","Only records the latest software command, not encoder feedback."],
    ["Register 4","Device status word; should not be interpreted as the real position."],
    ["Home flag","Register 5, used to judge the homing trigger state."],
    ["Captured scale factors","Absolute position ×102.4; step increment ×25.6."],
    ["Emergency-stop principle","On anomaly, Init OFF first, then use the hardware emergency stop."],
  ],["29060-29190"]);

  listSlide(pres,"MOKE Magnet | Differential Mode and Serial Ports","Differential Display",[
    ["Off","Shows the plain live view."],
    ["Plain differential","Current frame minus one fixed background."],
    ["Drift tracking","Compensates drift on top of background subtraction."],
    ["Re-acquire background","Rebuilds the background and resets the ROI‑time zero."],
    ["Add 100× marker","Marks in the 5× view the expected field of view after switching to 100×."],
  ],"KEPCO / Gaussmeter Connection",[
    ["Four serial-port dropdowns","Select the out-of-plane/in-plane BOP and gaussmeter ports respectively."],
    ["Refresh / Connect / Disconnect","Enumerates ports and manages the four serial links."],
    ["Physical selector tab","The GUI must be in the same out-of-plane or in-plane position as the physical selector."],
    ["Enable/zero current position","Enables the current position and zeros first; both channels must be zeroed before switching."],
    ["Zero both now","Zeros both magnet outputs at the same time."],
  ],["13994-14210"]);

  listSlide(pres,"MOKE Magnet | Single-Channel Output and Gauss Zeroing","Per Magnet Channel",[
    ["Target A + Write","Writes the target current."],
    ["Invert","Negates the current target current and writes it."],
    ["Zero current","Sets the BOP current to 0 A."],
    ["Zero field","Adjusts the current in closed loop so the corresponding gaussmeter approaches 0 G."],
    ["I / Field","Shows the current read-back and the gaussmeter reading of the current position."],
    ["Step A + Down/Up","Lowers or raises the current by the given increment."],
  ],"Readout and Interlock",[
    ["Read current position","Immediately reads the current and field of the current physical position."],
    ["Continuous","Auto-refreshes at the interval in s."],
    ["Interval s","Sets the auto-read period."],
    ["Non-current position","Shown as unavailable; the other channel's reading must not be mistaken for the current position."],
    ["Safety checkbox","Polarity, cooling, wiring and personnel safety must be confirmed before any non-zero output."],
    ["Loss of link","Stop the sweep/zero first, then disconnect and check serial-port occupation."],
  ],["14120-14210","14620-14710"]);

  listSlide(pres,"MOKE Magnet | Current Sweep and ROI Acquisition","Sweep Parameters",[
    ["Start / Stop / Step A","Generates the start→end→start current path."],
    ["Loop","Number of round-trip loops."],
    ["Settle s","Wait after reaching each current point."],
    ["Acquire s","Used for ROI averaging and synchronized image acquisition."],
    ["Start sweep","Shows the estimated time and runs in the background after confirmation."],
  ],"Run and Save",[
    ["Stop/zero","Terminates the sweep and zeros the current channel."],
    ["Save CSV + PNG","Choose a folder; saves loop data, the right-hand curve PNG and the parameter log."],
    ["Save processed images synchronously","Saves a processed image at every sweep point; increases I/O time."],
    ["Progress bar / Estimated remaining","Shows the current point count and ETA."],
    ["Right plot window","X axis is the magnet output current; the top axis shows the gaussmeter field."],
  ],["14211-14305","19500-20400"]);

  listSlide(pres,"MOKE Magnet | ROI, CLim and Image Processing","ROI / Curves",[
    ["A± / W± / H± / X± / Y±","Changes angle, width, height and center position by the editable step size."],
    ["Field","Selects out-of-plane or in-plane as the curve's field source."],
    ["Refresh s / Points / Apply","Sets the ROI‑time refresh period and maximum point count."],
    ["Clear curves","Clears the ROI‑time and field-sweep curves."],
    ["Manual / Auto CLim","Maps raw differential values manually, or estimates automatically from the visible region."],
    ["Histogram","Shows the distribution of raw differential values before CLim."],
  ],"Denoising, Exposure and Saving",[
    ["0/2/4/8 frame averaging","Temporal averaging of the raw differential data."],
    ["1×1/2×2/4×4/8×8 Binning","Spatial binning of the raw data."],
    ["Mean filter off/2×2/4×4","Sliding mean that affects ROI, sweeps and processed images alike."],
    ["Exposure ms + Apply exposure","Writes the entered exposure to the camera."],
    ["Start recording","Saves the current unmarked 8‑bit Camera Live view."],
    ["Three image save types","Display image, raw 16‑bit and raw differential image are saved separately and never mixed."],
  ],["14306-14590","17400-18100"]);

  listSlide(pres,"2410 | Connection, Resistance Monitoring and Manual Voltage","Connection and Emergency Stop",[
    ["Resource / Current limit","Enter the VISA address and the allowed current limit."],
    ["Connect / Disconnect","Manages the 2410 session and the green status light."],
    ["2410 zero / Output off","Zeros the voltage and turns output off."],
    ["Safety note","Voltage sweep up to 200 V; voltage limit stays at 5 V during resistance measurement."],
  ],"|R| Continuous Measurement / Manual Voltage",[
    ["Resistance safety confirmation","Confirm wiring and that the sample allows the test current."],
    ["Start continuous measurement / End measurement","Scrolling display of the absolute resistance over the last 20 s."],
    ["Set single V + Write","Directly sets one voltage point."],
    ["Invert / Zero","Negates the voltage or sets it to 0 V."],
    ["Step V + Down/Up","Adjusts the voltage by the given step size."],
  ],["14713-14830","20500-21000"]);

  listSlide(pres,"2410 | Round-Trip Sweep, ROI and Files","Voltage Sweep",[
    ["Start / Stop / Step V","Defines the start→end→start voltage path."],
    ["Settle s / Acquire s","Per-point wait and ROI averaging time."],
    ["Start round-trip sweep / Stop sweep","Starts the background sweep; output is turned off on stop."],
    ["Save processed images synchronously","Saves a processed image at every voltage point."],
    ["Save CSV + PNG","Saves Voltage‑ROI, the parameter log and the corresponding PNG; no separate ROI‑time PNG."],
  ],"ROI and Recording",[
    ["A/W/H/X/Y ±","Same ROI editing as MOKE."],
    ["Live refresh interval / Max points / Apply","Controls the ROI‑time display load."],
    ["Save processed image","Saves the current differential/processed result."],
    ["Save raw 16-bit image","Saves the raw camera data."],
    ["Start recording","Records Camera Live 8‑bit; the current voltage is marked below the video."],
    ["Right plot window","Records Voltage vs ROI only during a voltage sweep."],
  ],["14830-15110","21000-21600"]);

  screenshotSlide(pres,"6221 | Full Module Interface",IMG_6221_DATA,IMG_6221,"Current 6221 tab: continuous output, finite pulses, two pulse sweeps, ROI and dual plot windows.",["15111-15457"]);

  listSlide(pres,"6221 | Continuous Output and Finite Pulses","AC/DC Continuous Output",[
    ["Mode AC/DC","AC uses RMS current; the frequency box is grayed out when DC is selected."],
    ["Current / Frequency / Compliance","Sets the source output, current frequency and voltage protection."],
    ["Apply continuous output","Configures and turns on continuous output."],
    ["Stop / Zero","Turns output off and zeros the setting."],
    ["Live read-back","Shows mode, I readback, f readback, compliance, OUTP and compliance state."],
  ],"DC Unipolar Finite Pulses",[
    ["Pulse current","Toggles between 0 A and the set current."],
    ["High-level pulse width us","Sets the duration of the non-zero current."],
    ["Pulse count","Finite cycles; returns to 0 A and turns output off automatically at the end."],
    ["Compliance V","Protects the load; the actual current may be below the setting when in compliance."],
    ["Apply pulse output","Sends the official finite-waveform command sequence."],
    ["6221 stop now / Zero","Turns output off immediately in any mode."],
  ],["15111-15281","22400-23700"]);

  listSlide(pres,"6221 | Two Pulse Sweeps and ROI","Current Round-Trip Sweep",[
    ["Start / Stop / Step mA","Defines the round-trip pulse amplitude path."],
    ["Pulse width us","High-level time of each pulse."],
    ["Acquire/interval s","Wait after the pulse and average the ROI within this time."],
    ["Start current round-trip sweep","After start, the right plot's X axis is the pulse current."],
  ],"Fixed-Current Pulse Acquisition",[
    ["Fixed pulse mA / Pulse count","Specifies the repeat count at the same amplitude."],
    ["Pulse width us / Acquire interval s","Controls pulse width and the post-pulse ROI averaging window."],
    ["Start fixed-current pulse acquisition","The right plot's X axis switches to pulse index."],
    ["Save MOKE raw 16-bit synchronously","Saves a raw image at the end of the acquisition window after each pulse."],
    ["Save CSV","Saves pulse parameters and ROI data."],
    ["ROI/left plot","The left plot keeps showing ROI intensity vs time; the right plot records only pulse sweeps."],
  ],["15282-15457","23700-24400"]);

  listSlide(pres,"IV | Instrument Rows and Independent Connections","6221 / SR830",[
    ["6221 output AC/DC","Selects the source mode; frequency is not editable in DC."],
    ["Connect / Disconnect per instrument","6221, #8, #9, MFLI and 2182A each establish independent sessions."],
    ["SR830 input coupling AC/DC","Sets the input front-end coupling."],
    ["Harmonic / Read X/Y/R","Selects the harmonic and the recorded quantity."],
    ["Participate in sweep","Use only #8, only #9, or both."],
  ],"MFLI / 2182A",[
    ["Reference port","Aux Input 1/2 or Trigger In 1/2, written to the corresponding demodulator reference source."],
    ["Input coupling / Harmonic / Read","Configures AC/DC, harmonic and X/Y/R."],
    ["Participate in sweep (auto range)","Enables the MFLI during the sweep and adjusts the input range dynamically."],
    ["Impedance / Scaling","10 MΩ or 50 Ω selectable, plus a scaling factor."],
    ["2182A participate in sweep","Reads channel 1 voltage; theta is not plotted."],
  ],["15458-15720"]);

  listSlide(pres,"IV | Sweep Parameters, Trigger Link and Plot Windows","Current Sweep Parameters",[
    ["Start / Stop / Step current","Defines the 6221 RMS sweep path."],
    ["Frequency / Sampling Hz / Settle s","Controls reference frequency, sampling rate and per-point settling."],
    ["Measure per point s","Averaging time at each current point."],
    ["Pre-conditioning current / Pre-conditioning s","Pre-set before the formal sweep; starts directly when 0 s."],
    ["Compliance V / Plot X axis","Sets source protection and the I or I² X axis."],
  ],"Actions and Display",[
    ["Trigger Link output line","Selects the 6221 marker line; connect it to the selected SR830 REF IN/MFLI reference port."],
    ["Safety confirmation","Confirm the sample allows the AC/DC current and compliance."],
    ["Stop output now / Zero","Turns off the 6221."],
    ["Select folder and start sweep","Choose the directory first, then run with live saving."],
    ["Stop sweep","Terminates and performs cleanup."],
    ["Plot windows","Independent R/voltage, theta and I‑V per instrument; units switch automatically and history points are redrawn."],
  ],["15713-15957","24400-26900"]);

  listSlide(pres,"Gate‑VI | Instrument Connections and Interlock","Independent Connections",[
    ["2410 / 6221","Connect the gate pulse source and the read current source respectively."],
    ["SR830 #8/#9","Sets input coupling, harmonic, X/Y/R and sweep participation."],
    ["MFLI","Selects reference port, coupling, harmonic, read quantity, auto range, impedance and Scaling."],
    ["2182A","Channel 1, NPLC 1, auto range; sweep participation optional."],
  ],"Safety and Trigger Link",[
    ["Safety confirmation","Confirm the 2410 voltage pulses, 6221 current and sample safety."],
    ["Trigger Link output line","Sends the marker to the selected reference input."],
    ["Interlock","The 6221 must be OFF while the 2410 pulses/measures resistance; the 2410 must be OFF while reading voltage."],
    ["Stop 2410 / 6221 now","Turns both source outputs off at the same time."],
  ],["15958-16223"]);

  listSlide(pres,"Gate‑VI | Voltage Pulse Sweep and Data","Sweep Parameters",[
    ["Start / Stop / Step V","Defines the gate voltage pulse sequence."],
    ["Pulse width s / Post-pulse wait s","Controls the write pulse and the readout delay."],
    ["6221 current mA / Frequency Hz","Sets the read current; frequency is ignored in DC mode."],
    ["Sampling Hz / Pre-run s / Measure per point s","Sets readout sampling and averaging."],
    ["6221 compliance / Pulse threshold MΩ","Protects the current source and sets the gate-pulse decision threshold."],
  ],"Run and Plot Windows",[
    ["Select folder and start sweep","Selects the output directory and runs the complete gating sequence."],
    ["Stop sweep","Stops the thread and turns off the 2410/6221 outputs."],
    ["Status lights","Each instrument shows its connection state independently."],
    ["SR830/MFLI plot windows","Shows voltage/lock-in signal, theta and the Gate‑VI relation."],
    ["2182A plot window","Shows only Voltage vs time and Gate‑VI, no theta."],
  ],["16224-16450","26900-28600"]);

  listSlide(pres,"PPMS1 | Connection, Read-Back and Environment Control","PPMS1 Session",[
    ["Interface / IP / Port","Selects the MultiVu/QDInstrument back end; IP and TCP port are editable."],
    ["Connect PPMS / Disconnect","Establishes or releases the remote session."],
    ["Read current values","Refreshes temperature, field and status."],
    ["Green status light","Only means the session is available."],
    ["Safety confirmation","Must be checked before temperature and field setpoints are enabled."],
  ],"Temperature and Field",[
    ["Target K / Rate / Approach / Set temperature","5–380 K; ≤1 K/min in the 5–10 K range, ≤10 K/min elsewhere."],
    ["Target Oe / Rate / Approach / Mode / Set field","Range ±89900 Oe; rate must be <150 Oe/s."],
    ["Zero","Sets the PPMS target field to 0 Oe."],
    ["Step Oe + Down/Up","Adjusts the field by the given step size."],
    ["Current temperature / Current field","Shows the values and states such as Stable/Persistent."],
  ],["11304-11515"]);

  listSlide(pres,"PPMS1 | Forced Synchronous Field Sweep and External Instruments","Field Sweep",[
    ["Start / Stop / Step Oe","Defines the external field path."],
    ["Rate Oe/s / Loop / Settle s","Sets the field ramp rate, loop count and wait."],
    ["Start sweep / Stop and zero","The sweep must apply 6221 AC and measure at least one SR830 at the same time."],
    ["Save CSV","Saves environment values, source settings and SR830 voltages."],
    ["Progress bar / ETA","Shows completed points and estimated remaining time."],
  ],"Transport and Instruments",[
    ["Current RMS / Frequency / Compliance","Sets the 6221 AC readout."],
    ["Slot A / Slot B harmonic","Sets the harmonic of the two SR830s separately."],
    ["External instrument Connect / Disconnect","6221, two SR830s and the 2400 are managed independently."],
    ["SR830 address 8/9/10","Selectable per slot; duplicate addresses are not allowed."],
    ["6221 / 2400 output off now","Emergency shutdown of the source outputs."],
    ["Voltage vs field plot","Plots only the SR830s that are connected and participating."],
  ],["11516-11750","28600-29010"]);

  screenshotSlide(pres,"STFMR | Full Module Interface",IMG_STFMR_DATA,IMG_STFMR,"Current STFMR tab: three core instruments, 4886 output, 475 read-back, multi-frequency field sweep and measured-field curve.",["11751-12130"],[`${PROJECT} (STFMR validation boundary)`]);

  listSlide(pres,"STFMR | Connection, 4886 and LS475","Instrument Connections",[
    ["KEPCO4886","GPIB0::6::INSTR; connecting only claims the resource, with no query and no output write."],
    ["LS475","GPIB0::12::INSTR; used for real magnetic-field feedback."],
    ["PNA","GPIB0::16::INSTR; N5242A/N5227A."],
    ["SR830 / MFLI / SMB100A","Reserved independent connections and status lights; SMB defaults to GPIB0::28::INSTR."],
    ["Green/gray light","Connected/not connected respectively."],
  ],"Manual Output and Read-Back",[
    ["Safety confirmation","Confirm magnet polarity, cooling, wiring, front-panel limits and that no other control program is running."],
    ["Output mode","Select current or voltage."],
    ["Target / Limit / Apply output","Writes to the 4886; software limits ±10 A / ±20 V."],
    ["Show sent value","Shows only the cached latest command, not a 4886 read-back."],
    ["Zero / OFF","Sends 0 and turns output off."],
    ["Read once / Continuous read","Reads the field from the LS475; continuous period 0.5 s."],
  ],["11751-11940"],[`${PROJECT} (4886 trace-constrained behavior)`]);

  listSlide(pres,"STFMR | Multi-Frequency Field Sweep","Parameters",[
    ["Frequency start / stop / step GHz","E.g. 5→10 with step 1 runs 6 frequencies in turn."],
    ["PNA power dBm","Default −10 dBm; GUI upper limit 0 dBm."],
    ["Start / Stop / Step A","Sweeps the 4886 current; the X axis is the absolute value of the LS475 measured field."],
    ["Voltage limit V / Wait s","Sets the 4886 voltage limit and the per-point settling time."],
    ["S11/S12/S21/S22","Multi-select; combinations across source ports switch in order at the same field point, not strictly simultaneous sampling."],
  ],"Actions",[
    ["Turn off PNA RF at end","Turns off the excitation when the sweep ends or is stopped."],
    ["Start multi-frequency field sweep","Runs with frequency as the outer loop and field points as the inner loop."],
    ["Stop / Zero","Stops the thread, zeros the 4886 and turns output off."],
    ["Save CSV + PNG","Saves data for all frequencies/all S parameters and the field curves."],
    ["Progress bar","Shows total frequency × field × S-parameter progress and status."],
    ["Sampling after frequency change","After switching frequency, a new curve is created and settling is awaited, so transition points are not mixed into adjacent frequencies."],
  ],["11941-12040","12130-13750"]);

  listSlide(pres,"STFMR | Fixed-Field Frequency Sweep","Parameters and Feedback",[
    ["Fixed field G","Absolute value of the target field."],
    ["Frequency start / stop / step GHz","Defines the frequency sequence at a single field."],
    ["Field tolerance G","Default 10 G; feedback adjusts into tolerance before the frequency sweep starts."],
    ["Feedback wait s","Settling time given to the LS475 after each 4886 adjustment."],
    ["S parameters / PNA power","Reuses the selection and power setting above."],
  ],"Actions and Plots",[
    ["Fixed-field frequency sweep","First adjusts the 4886 with LS475 feedback, then acquires frequency by frequency."],
    ["Save frequency-sweep CSV + PNG","Saves Frequency‑S-parameter data and images."],
    ["Frequency-sweep progress bar","Shows feedback and frequency-point progress."],
    ["Field plot X axis","Uses |measured field|; 0 is always at the far left and both positive and negative fields increase to the right."],
    ["Frequency plot X axis","GHz; the Y axis is the selected S-parameter magnitude in dB."],
  ],["12040-12130","13250-13750"]);

  screenshotSlide(pres,"STFMR | Live Curve Example",IMG_STFMR_LAYOUT_DATA,IMG_STFMR_LAYOUT,"Live curve of S21 versus |measured field|; the data come from the 475 read-back, not from converting the 4886 current.",["13100-13380"],[`${IMG_STFMR_LAYOUT} (current GUI screenshot)`]);

  listSlide(pres,"Common ROI Editing | Button Meanings","Geometry Buttons",[
    ["A− / A+","Decrease/increase the ROI rotation angle."],
    ["W− / W+","Decrease/increase the ROI width."],
    ["H− / H+","Decrease/increase the ROI height."],
    ["X− / X+","Move the ROI center left/right."],
    ["Y− / Y+","Move the ROI center up/down."],
  ],"Step Sizes and Constraints",[
    ["A / W / H / X / Y step","All step sizes are editable; angle in °, position/size in px."],
    ["Shift + left-drag","The fastest way to create a new ROI."],
    ["ROI mean","Shows the ROI mean after the current processing chain."],
    ["During a sweep","ROI reselection is blocked during a 2410 sweep to avoid changing the data region."],
    ["Clear ROI","After clearing with the Camera Live shortcut, each module's curves wait for a new selection."],
  ],["16380-16540","8800-10100"]);

  listSlide(pres,"File Saving | Do Not Confuse the Data Layers","Images and Video",[
    ["Save current display image","Saves the current 8‑bit Camera Live display content, without markers."],
    ["Save raw 16-bit","Saves the raw 16‑bit camera data."],
    ["Save raw differential image","Saves the raw data layer after differential, binning and mean filter."],
    ["Save processed images synchronously","Saves a processed image at every sweep point; disk I/O becomes slower."],
    ["Camera Live 8-bit recording","Records the display layer at the set FPS; the video includes field/voltage experiment markers."],
  ],"CSV / PNG / log",[
    ["MOKE Save CSV + PNG","Saves magnet current, Gauss, ROI, curve snapshots and the complete parameter log."],
    ["2410 Save CSV + PNG","Saves Voltage‑ROI and the parameter log; no ROI‑time PNG."],
    ["6221 Save CSV","Saves the pulse path, pulse index and ROI mean."],
    ["PPMS1 Save CSV","Saves temperature, field, 6221 and SR830."],
    ["STFMR two save types","Multi-frequency field sweep and fixed-field frequency sweep each save CSV + PNG."],
  ],["14310-14580","14910-15060","15320-15420","11535-11575","11990-12100"]);

  listSlide(pres,"Stop, Zero and Exit | Verify per Module","Immediate Actions",[
    ["LED zero all","Zeros and turns off all four LED channels."],
    ["Motor Init OFF","Stops drive output; the mechanical emergency stop must remain reachable."],
    ["MOKE zero both now","Zeros both magnet channels."],
    ["2410 zero / Output off","Sets voltage to 0 and turns the source off."],
    ["6221 stop now / Zero","Turns off continuous and pulse outputs."],
    ["IV/Gate‑VI emergency stop","Turns off the 6221, or the 2410/6221 together."],
  ],"Sweep Cleanup",[
    ["PPMS1 stop/zero","Stops the field sweep and returns to zero; 6221 output is turned off."],
    ["STFMR stop/zero","Stops the sweep, zeros/turns off the 4886; turns off PNA RF according to the setting."],
    ["Program close","Tries module by module to stop, turn off outputs and release sessions."],
    ["Rotation stage on exit","Keeps its current position; does not auto-return to zero."],
    ["Final verification","After the program exits, check the instrument front panels to confirm all sources are really OFF."],
  ],["10800-11010","11100-11120","14750-14770","15260-15280","15740-15755","16200-16220","11870-11895"]);

  listSlide(pres,"Standard Start-Up Sequence","Before Power-On",[
    ["1 · Check hardware","Cables, polarity, cooling, mechanical limits, grounding, ranges and emergency stop."],
    ["2 · Close other controllers","Avoid LabVIEW, old Python or instrument software occupying resources at the same time."],
    ["3 · Launch the main program","Confirm the title is the current Transport_NH version."],
    ["4 · Connect one by one","Connect read-back instruments first, then output sources."],
  ],"Starting Measurement",[
    ["5 · Read zero points","Check that camera, Gauss, temperature, field and voltage/current are reasonable."],
    ["6 · Check safety confirmation","Check it only after the hardware checks are complete."],
    ["7 · Small-range trial run","Verify direction and read-back with 0 or the smallest safe value first."],
    ["8 · Set the sweep and estimate time","Verify start/stop, step, Loop, wait, acquisition and save directory."],
    ["9 · Run and observe","Watch progress, instrument front panels and abnormal states at the same time."],
  ],["10200-10990","11300-12130"]);

  listSlide(pres,"Common Problems and Handling","Connection",[
    ["Button still gray","Check the connection light, safety confirmation and whether all required instruments are present."],
    ["VISA/serial timeout","Stop output, close the occupying program, disconnect and reconnect; do not retry blindly in a row."],
    ["Resource enumerates but does not respond","Check terminator, address, driver and the instrument's remote/local state."],
    ["MFLI WARNING","Check reference port, lock-in frequency, input range, impedance and LabOne compatibility."],
    ["PPMS will not connect","Confirm MultiVu/QDInstrument Server, IP/port and the single-client limit."],
  ],"Data / Display",[
    ["No data in ROI curve","Select an ROI, connect the camera and confirm the corresponding processing/sweep mode."],
    ["No domains visible in differential","Re-acquire the background; check CLim, temporal averaging, binning, mean filter and exposure."],
    ["Video will not open","Prefer an MJPG/AVI-compatible player; the raw images/CSV remain the source data."],
    ["Curve unit jumps","Auto units rescale all history points, not only the new ones."],
    ["Sweep slows down","Synchronized images, short refresh periods and high point counts all increase load."],
  ],["17000-18100","20800-21600","23900-24400","24400-28600"]);

  listSlide(pres,"Runtime Dependencies | Which Module Needs What","Common Software",[
    ["Python 3.11","Development and packaging baseline; Windows runs need a matching environment or a standalone EXE package."],
    ["pyserial","LED, MOKE magnet and motor serial ports."],
    ["PyVISA + NI‑VISA","2410, 6221, SR830, 2182A, PNA, 4886, LS475, etc."],
    ["NumPy / Pillow / OpenCV","Image, ROI, video and data processing."],
    ["Tkinter","Main GUI and Camera Live."],
  ],"Dedicated Drivers",[
    ["PCO SDK / sc2_cam.dll","PCO camera."],
    ["Thorlabs Kinesis + pythonnet","K10CR1 rotation stage."],
    ["Zurich LabOne + zhinst","MFLI; API and Data Server versions must be compatible."],
    ["Quantum Design MultiVu/QDInstrument","PPMS1 environment control."],
    ["Instrument firmware/drivers","All modules still depend on correct hardware addresses and local drivers."],
  ],["18-120","9000-11000"],[`${PROJECT} (runtime and hardware validation notes)`]);

  listSlide(pres,"One-Page Pre-Operation Checklist","Outputs and Motion",[
    ["□ Output sources at 0 / OFF","LED, BOP, 2410, 6221, 4886."],
    ["□ Mechanical path safe","Motor, rotation stage, probes and limits."],
    ["□ Cooling and polarity correct","MOKE magnet and STFMR electromagnet."],
    ["□ No second control program","Especially LabVIEW, old scripts and instrument software."],
    ["□ Emergency stop reachable","A software emergency stop cannot replace the hardware one."],
  ],"Parameters and Data",[
    ["□ Addresses/ports correct","COM, GPIB, IP, TCP port."],
    ["□ Current/voltage limits and rates correct","Including PPMS temperature/field rates."],
    ["□ ROI and differential correct","Background, CLim, exposure, averaging and binning."],
    ["□ Sweep path correct","Start/stop, step, Loop, settle, acquire."],
    ["□ Enough space in save directory","Check especially before synchronized images and video."],
  ],["10200-10990","11300-16280"]);

  listSlide(pres,"Quick Roadmap | What Do I Want to Do?","Optics and MOKE",[
    ["View camera / adjust illumination","MOKE Light Source → Connect camera → LED/analyzer → Camera Live."],
    ["Focus/displacement","MOKE Motor → Init ON → Home/step."],
    ["View domain loops","MOKE Magnet → Differential → Confirm selector tab → Current sweep + ROI."],
    ["Electric-field control of MOKE","2410 → Round-trip voltage sweep + ROI/recording."],
    ["Pulse control of MOKE","6221 → Round-trip or fixed-current pulses + ROI."],
  ],"Transport and Microwave",[
    ["Non-local I‑V","IV → 6221 + SR830/MFLI/2182A."],
    ["Gate‑VI","Gate‑VI → 2410 pulses + 6221/lock-in/nanovoltmeter."],
    ["PPMS transport field sweep","PPMS1 → Environment connection + 6221 AC + SR830."],
    ["Multi-frequency FMR","STFMR → 4886/475/PNA → Multi-frequency field sweep."],
    ["Fixed-field frequency sweep","STFMR → Feedback to target field → Fixed-field frequency sweep."],
  ],["11020-11028","11300-16280"]);

  // Closing
  {
    const s=pres.slides.add(); s.background.fill=C.navy;
    addText(s,"Confirm the Hardware First, Then Allow Output",84,112,900,70,{fontSize:48,bold:true,color:C.white});
    addText(s,"A successful connection only means a software session is established; experiment safety and data credibility are still guaranteed jointly by wiring, limits, read-back and on-site verification.",88,220,920,96,{fontSize:24,color:"#CDE6EA",valign:"top"});
    addBox(s,88,362,910,124,"#20415D","#2B6073");
    addText(s,"Recommended Order",116,380,200,28,{fontSize:18,bold:true,color:"#9FE6E1"});
    addText(s,"Read-back instruments → Output sources → Small-range verification → Formal sweep → Save → Zero/OFF → Front-panel re-check",116,418,840,44,{fontSize:21,bold:true,color:C.white});
    s.images.add({dataUrl:ICON_DATA,alt:"Transport_NH icon",fit:"contain",position:{left:1070,top:500,width:120,height:120}});
    addText(s,"Transport_NH GUI User Guide · 2026‑08‑28",88,646,760,26,{fontSize:16,color:"#9FB4C2"});
    notes(s,["10200-10990"]);
  }

  pres.slides.items.forEach((s,i)=>footer(s,i+1));
  // Remove footer on cover and closing for cleaner composition.

  for (const [index, slide] of pres.slides.items.entries()) {
    const stem = `slide-${String(index+1).padStart(2,"0")}`;
    const png = await pres.export({ slide, format:"png", scale:1 });
    await fs.writeFile(`${RENDER}/${stem}.png`, new Uint8Array(await png.arrayBuffer()));
    const layout = await slide.export({ format:"layout" });
    await fs.writeFile(`${RENDER}/${stem}.layout.json`, await layout.text());
  }
  const montage = await pres.export({format:"webp",montage:true,scale:0.35});
  await fs.writeFile(`${RENDER}/deck-montage.webp`, new Uint8Array(await montage.arrayBuffer()));
  const pptx = await PresentationFile.exportPptx(pres);
  await pptx.save(OUT);
  console.log(`Wrote ${OUT} with ${pres.slides.items.length} slides`);
}

main().catch(err=>{ console.error(err); process.exitCode=1; });
