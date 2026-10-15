// Runs app.js's proposeLiftEnd (the UI's live preview rule) on cases from
// stdin and prints the results — compared with the server's rule in pytest.
const fs = require("fs");
const noop = () => {};
global.document = { getElementById: () => ({ addEventListener: noop, style: {}, classList: { add: noop, remove: noop } }),
                    querySelectorAll: () => [], createElement: () => ({}), addEventListener: noop };
global.location = { protocol: "http:", host: "x" };
const src = fs.readFileSync(process.argv[2], "utf8");
const proposeLiftEnd = new Function(src + "\n;return proposeLiftEnd;")();
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const out = cases.map(c => {
  const r = proposeLiftEnd(c.candidates, c.target, c.thickness, c.max_z);
  return { needed: r.needed, at: r.choice ? r.choice.at : null, reason: r.reason };
});
process.stdout.write(JSON.stringify(out));
