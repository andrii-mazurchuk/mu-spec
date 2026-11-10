import { expect, test } from "bun:test"
import { wrap } from "./spine-graph"

test("node titles wrap at word breaks and end in an ellipsis, never a mid-word cut", () => {
  expect(wrap("Track spending in my own currency", 20)).toEqual(["Track spending in my", "own currency"])
  expect(wrap("Charts in account currency · 1 test · and more words here", 20)).toEqual(["Charts in account", "currency · 1 test ·…"])
  expect(wrap("Supercalifragilisticexpialidocious", 10, 2)).toEqual(["Supercali…"])
  expect(wrap("", 20)).toEqual([])
})
