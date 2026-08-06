// trace.js registers itself as a browser global via a UMD wrapper (see its
// header comment); this just gives tsc a type for that global.
declare const Trace: typeof import("./trace.js");
