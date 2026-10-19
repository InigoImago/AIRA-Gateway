// What OpenCode does before it imports a TUI plugin: resolve `solid-js` and `@opentui/*` to the
// host's own copies and compile Solid's JSX. Without it the plugin file, which lives outside any
// package, could import neither.
import "@opentui/solid/runtime-plugin-support"
