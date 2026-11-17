// `npm run dev:all`: the mock API (port 8777) + Vite dev server (5173,
// proxying /tools /files /jobs /events /summary /peaks to the mock).
import { spawn } from "node:child_process";

const opts = { stdio: "inherit", shell: process.platform === "win32" };
const mock = spawn("node", ["mock/server.mjs"], opts);
const vite = spawn("npx", ["vite"], opts);
const stop = () => {
  mock.kill();
  vite.kill();
  process.exit();
};
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
mock.on("exit", stop);
vite.on("exit", stop);
