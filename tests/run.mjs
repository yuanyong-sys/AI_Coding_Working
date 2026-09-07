import { browserEndToEnd } from "./browser-e2e.mjs";

const tests = [
  ["Vue and FastAPI execute all public page flows", browserEndToEnd]
];

let failed = 0;
for (const [name, test] of tests) {
  try {
    await test();
    console.log(`✓ ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`✗ ${name}`);
    console.error(error);
  }
}
if (failed > 0) process.exitCode = 1;
