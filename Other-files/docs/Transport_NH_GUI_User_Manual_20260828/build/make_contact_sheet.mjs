import fs from "node:fs/promises";
import sharp from "sharp";

const dir = "/Users/nbb/Desktop/2-Posdoc_Heng/0-Codex/3-Transport/docs/Transport_NH_GUI_User_Manual_20260828/build/rendered";
const files = (await fs.readdir(dir)).filter((name) => /^slide-\d+\.png$/.test(name)).sort();
const thumbW = 320;
const thumbH = 180;
const cols = 4;
const rows = Math.ceil(files.length / cols);
const tiles = [];
for (let i = 0; i < files.length; i += 1) {
  const data = await sharp(`${dir}/${files[i]}`).resize(thumbW, thumbH, { fit: "contain", background: "white" }).png().toBuffer();
  tiles.push({ input: data, left: (i % cols) * thumbW, top: Math.floor(i / cols) * thumbH });
}
await sharp({ create: { width: cols * thumbW, height: rows * thumbH, channels: 3, background: "#dfe7eb" } })
  .composite(tiles)
  .jpeg({ quality: 88 })
  .toFile(`${dir}/contact-sheet.jpg`);
