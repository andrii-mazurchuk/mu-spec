import { createCn } from "cn/config"

// Components import cn from here, never from "cn": the project's type scale (text-xs…) are font sizes.
export const cn = createCn({ extend: { classGroups: { "font-size": [{ text: ["xs","sm","base","lg","xl","2xl"] }] } } })
