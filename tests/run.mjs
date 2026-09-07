import {
  businessStateSurvivesRestart,
  publicPagesUseSharedState,
  resetRequiresConfirmationAndIsRepeatableAndAudited
} from "./poc-baseline.test.mjs";
import { browserEndToEnd } from "./browser-e2e.mjs";

const tests = [
  ["public entry links to four POC-labelled pages backed by the shared state API", publicPagesUseSharedState],
  ["business state survives service restart", businessStateSurvivesRestart],
  ["reset requires confirmation and is repeatable and audited", resetRequiresConfirmationAndIsRepeatableAndAudited],
  ["real browser executes all public page flows", browserEndToEnd]
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
