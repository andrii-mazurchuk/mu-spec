import { expect, test } from "bun:test"
import { addSample, phaseOf, summary, timeLeft, type Emission, type Sample } from "./run"

const p1 = (processed: number): Emission => ({ running: true, phase: "running", step: "creating", total: 120, processed })
const p2 = (wired: number): Emission => ({ running: true, phase: "running", step: "wiring", total: 120, processed: 120, to_wire: 210, wired })

test("phase 2 starts only once to_wire is known; percent is whole and per phase", () => {
  expect(phaseOf(p1(60))).toEqual({ phase: 1, done: 60, total: 120, pct: 50 })
  expect(phaseOf({ ...p2(0), to_wire: 0 }).phase).toBe(1)
  expect(phaseOf(p2(37))).toEqual({ phase: 2, done: 37, total: 210, pct: 17 })
  expect(phaseOf({ running: true, phase: "running", total: null }).pct).toBe(0)
})

test("time left: estimating first, then whole minutes from the recent pace, under a minute near the end", () => {
  let s: Sample[] = []
  for (let i = 0; i < 3; i++) s = addSample(s, p1(10 + 2 * i), i * 900)
  expect(timeLeft(s, p1(14))).toBe("estimating time left")
  s = addSample(s, p1(16), 2700) // 6 units in 2.7 s: 104 left ≈ 47 s
  expect(timeLeft(s, p1(16))).toBe("under a minute left on the issues at this pace; the order is timed once it starts")
  let w: Sample[] = []
  for (let i = 0; i < 5; i++) w = addSample(s, p2(i), i * 900) // phase change drops phase-1 samples
  expect(w.every(x => x.phase === 2)).toBe(true)
  w = []; for (let i = 0; i < 5; i++) w = addSample(w, p2(i), i * 900) // 1 per 0.9 s, 206 left ≈ 185 s
  expect(timeLeft(w, p2(4))).toBe("about 3 min left at this pace")
  const stalled = [0, 900, 1800, 2700].map(t => ({ t, phase: 2 as const, done: 4 }))
  expect(timeLeft(stalled, p2(4))).toBe("estimating time left")
})

test("summary says what landed and what did not", () => {
  expect(summary({ created: 95, adopted: 14, edited: 9, wired: 209, failed: 2, unwired: 1 }))
    .toBe("Shipped 95 issues, reused 14, edited 9 and declared 209 orders. 2 failed. 1 order could not be declared.")
  expect(summary({ created: 1, wired: 1 })).toBe("Shipped 1 issue and declared 1 order.")
})
